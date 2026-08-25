"""Inventory routes for local samples, staged samples, imports, workflows, and files."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

import cortex.db as _db
from cortex.db_helpers import _resolve_project_dir
from cortex.dependencies import require_project_access
from cortex.list_commands import (
    ListCommand,
    _list_file_rows,
    _list_imported_sample_rows,
    _list_local_sample_rows,
    _list_staged_sample_rows,
    _list_workflow_rows,
)

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("/samples")
async def list_local_samples(request: Request):
    user = request.state.user
    session = _db.SessionLocal()
    try:
        items = _list_local_sample_rows(session, user.id)
        return {"items": items, "count": len(items)}
    finally:
        session.close()


@router.get("/staged")
async def list_staged_samples(
    request: Request,
    profile: str | None = Query(None),
):
    user = request.state.user
    session = _db.SessionLocal()
    try:
        items = _list_staged_sample_rows(session, user.id, profile or "")
        return {"items": items, "count": len(items)}
    finally:
        session.close()


@router.get("/imported")
async def list_imported_samples(request: Request):
    user = request.state.user
    session = _db.SessionLocal()
    try:
        items = _list_imported_sample_rows(session, user.id)
        return {"items": items, "count": len(items)}
    finally:
        session.close()


@router.get("/workflows")
async def list_project_workflows(
    project_id: str = Query(..., min_length=1),
    request: Request = None,
):
    user = request.state.user
    require_project_access(project_id, user, min_role="viewer")

    session = _db.SessionLocal()
    try:
        project_dir = _resolve_project_dir(session, user, project_id)
        items = _list_workflow_rows(session, project_id, project_dir=str(project_dir))
        return {
            "items": items,
            "count": len(items),
            "project_id": project_id,
            "project_dir": str(project_dir),
        }
    finally:
        session.close()


@router.get("/files")
async def list_inventory_files(
    project_id: str = Query(..., min_length=1),
    target: str = Query(""),
    workflow_ref: str = Query(""),
    project_scope: bool = Query(False),
    max_depth: int | None = Query(None, ge=1, le=25),
    request: Request = None,
):
    user = request.state.user
    require_project_access(project_id, user, min_role="viewer")

    session = _db.SessionLocal()
    try:
        project_dir = _resolve_project_dir(session, user, project_id)
    finally:
        session.close()

    cleaned_workflow_ref = str(workflow_ref or "").strip().strip("/")
    cleaned_target = str(target or "").strip().strip("/")
    if cleaned_workflow_ref and cleaned_target:
        target_ref = f"{cleaned_workflow_ref}/{cleaned_target}"
    else:
        target_ref = cleaned_workflow_ref or cleaned_target

    try:
        work_dir, items, file_count = await _list_file_rows(
            ListCommand(
                action="files",
                target_ref=target_ref,
                project_scope=project_scope,
                max_depth=max_depth,
            ),
            history_blocks=None,
            project_dir=str(project_dir),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "items": items,
        "count": file_count,
        "project_id": project_id,
        "project_dir": str(project_dir),
        "work_dir": work_dir,
    }