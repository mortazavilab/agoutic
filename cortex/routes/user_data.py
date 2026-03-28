"""
User Data routes — central data folder management.

Provides a CRUD API for files in the user's central data folder
(``AGOUTIC_DATA/users/{username}/data/``), including metadata editing,
project linking/unlinking, and force re-download.
"""

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request, Query
from sqlalchemy import select

import cortex.db as _db
from cortex.models import (
    UserFile, UserFileProjectLink, Project, ProjectBlock,
)
from cortex.schemas import (
    UserFileOut, UserFileUpdate, UserFileLinkRequest, UserFileRedownloadRequest,
)
from cortex.user_jail import get_user_data_dir, create_project_file_symlink
from cortex.routes.files import (
    _compute_md5, _register_user_file, _link_file_to_project,
    _find_existing_user_file, _already_linked,
)
from common.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/user/data", tags=["user-data"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user_file_to_out(session, uf: UserFile) -> dict:
    """Convert a UserFile row + its project links to a JSON-serialisable dict."""
    # Fetch linked projects
    links = session.execute(
        select(UserFileProjectLink).where(
            UserFileProjectLink.user_file_id == uf.id
        )
    ).scalars().all()

    projects = []
    for lnk in links:
        proj = session.execute(
            select(Project).where(Project.id == lnk.project_id)
        ).scalar_one_or_none()
        rel_link_path = f"data/{uf.filename}"
        if proj and proj.slug and lnk.symlink_path:
            marker = f"/{proj.slug}/"
            if marker in lnk.symlink_path:
                rel_link_path = lnk.symlink_path.split(marker, 1)[1]
        projects.append({
            "project_id": lnk.project_id,
            "project_name": proj.name if proj else None,
            "symlink_path": rel_link_path,
            "linked_at": str(lnk.linked_at) if lnk.linked_at else None,
        })

    tags = None
    if uf.tags_json:
        try:
            tags = json.loads(uf.tags_json)
        except (json.JSONDecodeError, TypeError):
            tags = None

    return {
        "id": uf.id,
        "filename": uf.filename,
        "md5_hash": uf.md5_hash,
        "size_bytes": uf.size_bytes,
        "source": uf.source,
        "source_url": uf.source_url,
        "encode_accession": uf.encode_accession,
        "sample_name": uf.sample_name,
        "organism": uf.organism,
        "tissue": uf.tissue,
        "tags": tags,
        "disk_path": f"data/{uf.filename}",
        "created_at": str(uf.created_at) if uf.created_at else None,
        "updated_at": str(uf.updated_at) if uf.updated_at else None,
        "projects": projects,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("")
async def list_user_files(
    request: Request,
    include_untracked: bool = Query(False, description="Include files present on disk but missing DB records"),
):
    """List all files in the user's central data folder."""
    user = request.state.user
    session = _db.SessionLocal()
    try:
        rows = session.execute(
            select(UserFile)
            .where(UserFile.user_id == user.id)
            .order_by(UserFile.filename)
        ).scalars().all()

        tracked_paths = {
            str(Path(uf.disk_path).resolve(strict=False))
            for uf in rows
            if uf.disk_path
        }

        files = [_user_file_to_out(session, uf) for uf in rows]
        for item in files:
            item["tracked"] = True

        if include_untracked:
            username = getattr(user, "username", None)
            if username:
                central_dir = get_user_data_dir(username)

                for path in sorted(central_dir.rglob("*")):
                    if not path.is_file():
                        continue
                    resolved = str(path.resolve(strict=False))
                    if resolved in tracked_paths:
                        continue

                    stat = path.stat()
                    ts = datetime.fromtimestamp(stat.st_mtime).isoformat()
                    files.append(
                        {
                            "id": f"untracked::{path.relative_to(central_dir)}",
                            "filename": path.name,
                            "md5_hash": None,
                            "size_bytes": stat.st_size,
                            "source": "filesystem",
                            "source_url": None,
                            "encode_accession": None,
                            "sample_name": None,
                            "organism": None,
                            "tissue": None,
                            "tags": None,
                            "disk_path": str(path.relative_to(central_dir)),
                            "created_at": ts,
                            "updated_at": ts,
                            "projects": [],
                            "tracked": False,
                        }
                    )

                files.sort(key=lambda item: (item.get("filename") or "").lower())

        return {"files": files, "count": len(files)}
    finally:
        session.close()


@router.get("/{file_id}")
async def get_user_file(file_id: str, request: Request):
    """Get details for a single file in the user's central data folder."""
    user = request.state.user
    session = _db.SessionLocal()
    try:
        uf = session.execute(
            select(UserFile).where(
                UserFile.id == file_id,
                UserFile.user_id == user.id,
            )
        ).scalar_one_or_none()
        if not uf:
            raise HTTPException(status_code=404, detail="File not found")
        return _user_file_to_out(session, uf)
    finally:
        session.close()


@router.patch("/{file_id}")
async def update_user_file_metadata(file_id: str, body: UserFileUpdate, request: Request):
    """Update metadata (sample name, organism, tissue, tags) for a file."""
    user = request.state.user
    session = _db.SessionLocal()
    try:
        uf = session.execute(
            select(UserFile).where(
                UserFile.id == file_id,
                UserFile.user_id == user.id,
            )
        ).scalar_one_or_none()
        if not uf:
            raise HTTPException(status_code=404, detail="File not found")

        if body.sample_name is not None:
            uf.sample_name = body.sample_name
        if body.organism is not None:
            uf.organism = body.organism
        if body.tissue is not None:
            uf.tissue = body.tissue
        if body.tags is not None:
            uf.tags_json = json.dumps(body.tags)

        from sqlalchemy import func
        uf.updated_at = func.now()
        session.commit()

        return _user_file_to_out(session, uf)
    finally:
        session.close()


@router.post("/{file_id}/link")
async def link_file_to_project(file_id: str, body: UserFileLinkRequest, request: Request):
    """Link a central file to a project (creates symlink in project data/)."""
    user = request.state.user
    session = _db.SessionLocal()
    try:
        uf = session.execute(
            select(UserFile).where(
                UserFile.id == file_id,
                UserFile.user_id == user.id,
            )
        ).scalar_one_or_none()
        if not uf:
            raise HTTPException(status_code=404, detail="File not found")

        project = session.execute(
            select(Project).where(Project.id == body.project_id)
        ).scalar_one_or_none()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        if _already_linked(session, uf.id, body.project_id):
            return {"status": "already_linked", "file_id": uf.id, "project_id": body.project_id}

        username = getattr(user, "username", None)
        slug = project.slug
        if not username or not slug:
            raise HTTPException(status_code=400, detail="Cannot link — username or project slug missing")

        central_path = Path(uf.disk_path)
        if not central_path.exists():
            raise HTTPException(status_code=410, detail="Central file missing from disk")

        sym = create_project_file_symlink(username, slug, uf.filename, central_path)
        _link_file_to_project(
            session,
            user_file=uf,
            project_id=body.project_id,
            symlink_path=sym,
        )

        return {
            "status": "linked",
            "file_id": uf.id,
            "project_id": body.project_id,
            "symlink": f"data/{uf.filename}",
        }
    finally:
        session.close()


@router.post("/{file_id}/unlink")
async def unlink_file_from_project(file_id: str, body: UserFileLinkRequest, request: Request):
    """Unlink a file from a project (remove symlink + DB link, keep central file)."""
    user = request.state.user
    session = _db.SessionLocal()
    try:
        uf = session.execute(
            select(UserFile).where(
                UserFile.id == file_id,
                UserFile.user_id == user.id,
            )
        ).scalar_one_or_none()
        if not uf:
            raise HTTPException(status_code=404, detail="File not found")

        link = session.execute(
            select(UserFileProjectLink).where(
                UserFileProjectLink.user_file_id == file_id,
                UserFileProjectLink.project_id == body.project_id,
            )
        ).scalar_one_or_none()
        if not link:
            raise HTTPException(status_code=404, detail="Link not found")

        # Remove symlink from disk
        sym_path = Path(link.symlink_path)
        if sym_path.is_symlink():
            sym_path.unlink()

        session.delete(link)
        session.commit()

        return {"status": "unlinked", "file_id": uf.id, "project_id": body.project_id}
    finally:
        session.close()


@router.delete("/{file_id}")
async def delete_user_file(file_id: str, request: Request):
    """Delete a file from the central data folder and all project symlinks."""
    user = request.state.user
    session = _db.SessionLocal()
    try:
        uf = session.execute(
            select(UserFile).where(
                UserFile.id == file_id,
                UserFile.user_id == user.id,
            )
        ).scalar_one_or_none()
        if not uf:
            raise HTTPException(status_code=404, detail="File not found")

        # Remove all project symlinks
        links = session.execute(
            select(UserFileProjectLink).where(
                UserFileProjectLink.user_file_id == file_id,
            )
        ).scalars().all()
        removed_symlinks = []
        for lnk in links:
            sym = Path(lnk.symlink_path)
            if sym.is_symlink():
                sym.unlink()
                removed_symlinks.append(str(sym))
            session.delete(lnk)

        # Remove central file from disk
        central = Path(uf.disk_path)
        if central.exists():
            central.unlink()

        session.delete(uf)
        session.commit()

        return {
            "status": "deleted",
            "file_id": file_id,
            "removed_symlinks": [Path(s).name for s in removed_symlinks],
        }
    finally:
        session.close()


@router.post("/{file_id}/redownload")
async def redownload_file(file_id: str, body: UserFileRedownloadRequest, request: Request):
    """Force re-download a file from its original source URL.

    Requires ``force: true`` in the request body as explicit confirmation.
    """
    user = request.state.user
    if not body.force:
        raise HTTPException(status_code=400, detail="Must set force=true to re-download")

    session = _db.SessionLocal()
    try:
        uf = session.execute(
            select(UserFile).where(
                UserFile.id == file_id,
                UserFile.user_id == user.id,
            )
        ).scalar_one_or_none()
        if not uf:
            raise HTTPException(status_code=404, detail="File not found")
        if not uf.source_url:
            raise HTTPException(status_code=400, detail="File has no source URL — cannot re-download")

        dest = Path(uf.disk_path)
        url = uf.source_url

        # Download in background
        import hashlib

        async def _redownload():
            sess = _db.SessionLocal()
            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(600.0)) as client:
                    async with client.stream("GET", url) as resp:
                        resp.raise_for_status()
                        md5h = hashlib.md5()
                        file_bytes = 0
                        with open(dest, "wb") as f:
                            async for chunk in resp.aiter_bytes(chunk_size=65536):
                                f.write(chunk)
                                md5h.update(chunk)
                                file_bytes += len(chunk)

                rec = sess.execute(
                    select(UserFile).where(UserFile.id == file_id)
                ).scalar_one_or_none()
                if rec:
                    rec.md5_hash = md5h.hexdigest()
                    rec.size_bytes = file_bytes
                    from sqlalchemy import func as _fn
                    rec.updated_at = _fn.now()
                    sess.commit()
                logger.info("Redownload complete", file_id=file_id, bytes=file_bytes)
            except Exception as e:
                logger.error("Redownload failed", file_id=file_id, error=str(e))
            finally:
                sess.close()

        asyncio.create_task(_redownload())

        return {"status": "redownloading", "file_id": file_id, "source_url": url}
    finally:
        session.close()
