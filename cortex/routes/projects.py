"""
Project management routes — extracted from cortex/app.py (Tier 3).

Handles project CRUD, stats, file listing, token/disk usage,
and project deletion.
"""

import datetime
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, desc, func, text

import cortex.config as _cfg
import cortex.db as _db
import cortex.db_helpers as _dbh
from cortex.dependencies import require_project_access
from cortex.models import (
    Project, ProjectAccess, ProjectBlock, Conversation,
    ConversationMessage, User,
)
from cortex.schemas import ProjectTaskListOut, ProjectTaskOut, ProjectTaskUpdate
from cortex.task_service import build_task_sections, sync_project_tasks, task_to_dict, update_task_action
from common.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()


# --- Project Schemas ---

class ProjectCreateRequest(BaseModel):
    name: str


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    is_archived: Optional[bool] = None


def _normalize_project_name(name: str) -> str:
    """Normalize project name for duplicate checks."""
    return " ".join(name.strip().split())


# --- Slug helpers ---

def _slugify(text: str, max_len: int = 40) -> str:
    """Convert arbitrary text to a filesystem-safe slug."""
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-")
    return s[:max_len] or "project"


def _dedup_slug(session, owner_id: str, slug: str, exclude_project_id: str | None = None) -> str:
    """Ensure slug is unique among this owner's projects."""
    for i in range(0, 10000):
        candidate = slug if i == 0 else f"{slug}-{i}"
        query = select(Project).where(Project.owner_id == owner_id, Project.slug == candidate)
        if exclude_project_id:
            query = query.where(Project.id != exclude_project_id)
        if not session.execute(query).scalar_one_or_none():
            return candidate
    raise HTTPException(status_code=500, detail="Could not generate unique slug")


# --- Project CRUD Endpoints ---

@router.post("/projects")
async def create_project(req: ProjectCreateRequest, request: Request):
    """
    Create a new project with a server-generated UUID.
    Atomic: mkdir first (idempotent), then DB insert in a single commit.
    """
    from cortex.user_jail import get_user_project_dir, get_user_project_dir_by_uuid

    user = request.state.user
    project_id = str(uuid.uuid4())

    # 2. Atomic DB write: project row + owner access row in one commit
    session = _db.SessionLocal()
    try:
        # Generate unique slug
        slug = _slugify(req.name)
        slug = _dedup_slug(session, user.id, slug)

        # 1. Create filesystem directory
        # Use slug-based path if user has a username, otherwise fall back to UUID
        if user.username:
            project_dir = get_user_project_dir(user.username, slug)
            (project_dir / "data").mkdir(exist_ok=True)
        else:
            get_user_project_dir_by_uuid(user.id, project_id)

        now = datetime.datetime.utcnow()
        project = Project(
            id=project_id,
            name=req.name,
            slug=slug,
            owner_id=user.id,
            is_public=False,
            is_archived=False,
            created_at=now,
            updated_at=now,
        )
        session.add(project)

        access = ProjectAccess(
            id=str(uuid.uuid4()),
            user_id=user.id,
            project_id=project_id,
            project_name=req.name,
            role="owner",
            last_accessed=now,
        )
        session.add(access)

        # Also set as user's last project
        user_obj = session.execute(select(User).where(User.id == user.id)).scalar_one_or_none()
        if user_obj:
            user_obj.last_project_id = project_id

        session.commit()  # Single commit — both rows or neither

        logger.info("Project created", project_id=project_id, name=req.name, slug=slug, user=user.email)
        return {
            "id": project_id,
            "name": req.name,
            "slug": slug,
            "created_at": str(now),
        }
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create project: {str(e)}")
    finally:
        session.close()


@router.get("/projects")
async def list_projects(request: Request, include_archived: bool = False):
    """
    List all projects the user owns or has been granted access to.
    Returns metadata: name, created date, last activity, role.
    """
    user = request.state.user

    session = _db.SessionLocal()
    try:
        # Get all project_access records for this user
        query = select(ProjectAccess)\
            .where(ProjectAccess.user_id == user.id)\
            .order_by(desc(ProjectAccess.last_accessed))

        result = session.execute(query)
        access_records = result.scalars().all()

        projects = []
        for acc in access_records:
            # Try to get the full Project record (may not exist for legacy projects)
            proj = session.execute(
                select(Project).where(Project.id == acc.project_id)
            ).scalar_one_or_none()

            is_archived = proj.is_archived if proj else False
            if is_archived and not include_archived:
                continue

            # Lightweight job count from dogme_jobs table
            try:
                jcount = session.execute(
                    text("SELECT COUNT(*) FROM dogme_jobs WHERE project_id = :pid"),
                    {"pid": acc.project_id}
                ).scalar() or 0
            except Exception:
                jcount = None

            projects.append({
                "id": acc.project_id,
                "name": proj.name if proj else acc.project_name,
                "slug": proj.slug if proj else None,
                "role": acc.role if hasattr(acc, 'role') else "owner",
                "is_archived": is_archived,
                "is_public": proj.is_public if proj else False,
                "created_at": str(proj.created_at) if proj else None,
                "last_accessed": str(acc.last_accessed),
                "job_count": jcount,
            })

        return {"projects": projects}
    finally:
        session.close()


@router.patch("/projects/{project_id}")
async def update_project(project_id: str, req: ProjectUpdateRequest, request: Request):
    """
    Update project metadata (rename, archive, change slug). Requires owner role.
    When name changes, slug auto-updates and directory is renamed on disk.
    """
    from cortex.user_jail import rename_project_dir

    user = request.state.user
    require_project_access(project_id, user, min_role="owner")

    session = _db.SessionLocal()
    try:
        project = session.execute(
            select(Project).where(Project.id == project_id)
        ).scalar_one_or_none()

        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        old_slug = project.slug
        old_name = project.name

        if req.name is not None:
            normalized_name = _normalize_project_name(req.name)
            if not normalized_name:
                raise HTTPException(status_code=422, detail="Project name cannot be empty")

            duplicate = session.execute(
                select(Project.id)
                .where(Project.owner_id == project.owner_id)
                .where(func.lower(func.trim(Project.name)) == normalized_name.lower())
                .where(Project.id != project_id)
            ).scalar_one_or_none()
            if duplicate:
                raise HTTPException(status_code=409, detail="A project with this name already exists")

            project.name = normalized_name

            # Keep denormalized access rows in sync for all members.
            access_rows = session.execute(
                select(ProjectAccess).where(ProjectAccess.project_id == project_id)
            ).scalars().all()
            for access in access_rows:
                access.project_name = normalized_name

            # Auto-sync slug from new name (unless explicit slug provided)
            if req.slug is None and normalized_name != old_name:
                new_auto_slug = _slugify(normalized_name)
                new_auto_slug = _dedup_slug(session, user.id, new_auto_slug, exclude_project_id=project_id)
                project.slug = new_auto_slug

        if req.slug is not None:
            # Explicit slug change
            slug_clean = req.slug.strip().lower()
            slug_clean = re.sub(r"[^a-z0-9_-]", "-", slug_clean).strip("-")
            if not slug_clean:
                raise HTTPException(status_code=422, detail="Invalid slug")
            slug_clean = _dedup_slug(session, user.id, slug_clean, exclude_project_id=project_id)
            project.slug = slug_clean

        if req.is_archived is not None:
            project.is_archived = req.is_archived

        project.updated_at = datetime.datetime.utcnow()

        # Rename directory on disk if slug changed
        new_slug = project.slug
        if old_slug and new_slug and old_slug != new_slug and user.username:
            try:
                rename_project_dir(user.username, old_slug, new_slug)
                # Update nextflow_work_dir in dogme_jobs
                old_prefix = str(_cfg.AGOUTIC_DATA / "users" / user.username / old_slug)
                new_prefix = str(_cfg.AGOUTIC_DATA / "users" / user.username / new_slug)
                try:
                    session.execute(
                        text(
                            "UPDATE dogme_jobs SET nextflow_work_dir = "
                            "REPLACE(nextflow_work_dir, :old, :new) "
                            "WHERE nextflow_work_dir LIKE :pattern"
                        ),
                        {"old": old_prefix, "new": new_prefix, "pattern": old_prefix + "%"},
                    )
                except Exception:
                    pass  # dogme_jobs may not be in this DB
            except PermissionError as e:
                raise HTTPException(status_code=409, detail=str(e))

        # Record rename in memory system for audit trail
        if old_name and project.name != old_name:
            try:
                from cortex.memory_service import create_memory
                create_memory(
                    session,
                    user_id=user.id,
                    project_id=project_id,
                    content=f"Project renamed from \"{old_name}\" to \"{project.name}\"",
                    category="finding",
                    source="system",
                )
                if old_slug and new_slug != old_slug:
                    create_memory(
                        session,
                        user_id=user.id,
                        project_id=project_id,
                        content=f"Project folder renamed from \"{old_slug}\" to \"{new_slug}\"",
                        category="finding",
                        source="system",
                    )
            except Exception:
                pass  # memory system failure should not block rename

        session.commit()

        logger.info("Project updated", project_id=project_id, name=project.name, slug=new_slug, user=user.email)
        return {"status": "ok", "id": project_id, "name": project.name, "slug": new_slug}
    finally:
        session.close()


@router.get("/user/last-project")
async def get_last_project(request: Request):
    """Get user's last active project."""
    user = request.state.user

    session = _db.SessionLocal()
    try:
        user_query = select(User).where(User.id == user.id)
        result = session.execute(user_query)
        user_obj = result.scalar_one_or_none()

        if user_obj and user_obj.last_project_id:
            return {
                "last_project_id": user_obj.last_project_id
            }

        return {"last_project_id": None}
    finally:
        session.close()


@router.put("/user/last-project")
async def set_last_project(request: Request):
    """Update user's last active project (called on sidebar switch)."""
    user = request.state.user
    body = await request.json()
    project_id = body.get("project_id")
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id required")

    session = _db.SessionLocal()
    try:
        user_obj = session.execute(
            select(User).where(User.id == user.id)
        ).scalar_one_or_none()
        if user_obj:
            user_obj.last_project_id = project_id
            session.commit()
        return {"status": "ok"}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@router.get("/user/projects")
async def get_user_projects(request: Request):
    """Get all projects the user has accessed."""
    user = request.state.user

    session = _db.SessionLocal()
    try:
        query = select(ProjectAccess)\
            .where(ProjectAccess.user_id == user.id)\
            .order_by(desc(ProjectAccess.last_accessed))

        result = session.execute(query)
        projects = result.scalars().all()

        return {
            "projects": [
                {
                    "id": proj.project_id,
                    "name": proj.project_name,
                    "last_accessed": proj.last_accessed
                }
                for proj in projects
            ]
        }
    finally:
        session.close()


@router.post("/projects/{project_id}/access")
async def record_project_access(project_id: str, request: Request, project_name: str = None):
    """Record that user accessed a project. Requires existing access."""
    user = request.state.user
    require_project_access(project_id, user, min_role="viewer")

    session = _db.SessionLocal()
    try:
        await _dbh.track_project_access(session, user.id, project_id, project_name)
        return {"status": "ok"}
    finally:
        session.close()


@router.get("/projects/{project_id}/tasks", response_model=ProjectTaskListOut)
async def get_project_tasks(project_id: str, request: Request):
    """Return persistent project tasks grouped for the UI."""
    user = request.state.user
    require_project_access(project_id, user, min_role="viewer")

    session = _db.SessionLocal()
    try:
        tasks = sync_project_tasks(session, project_id)
        return {
            "project_id": project_id,
            "sections": build_task_sections(tasks),
        }
    finally:
        session.close()


@router.patch("/projects/{project_id}/tasks/{task_id}", response_model=ProjectTaskOut)
async def patch_project_task(project_id: str, task_id: str, body: ProjectTaskUpdate, request: Request):
    """Apply a user action to a project task."""
    user = request.state.user
    require_project_access(project_id, user, min_role="editor")

    session = _db.SessionLocal()
    try:
        task = update_task_action(session, project_id, task_id, body.action)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return task_to_dict(task)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    finally:
        session.close()


# =============================================================================
# PROJECT DASHBOARD ENDPOINTS
# =============================================================================

@router.get("/projects/{project_id}/stats")
async def get_project_stats(project_id: str, request: Request):
    """Get detailed stats for a project: job count, file count, disk usage."""
    user = request.state.user
    require_project_access(project_id, user, min_role="viewer")

    session = _db.SessionLocal()
    try:
        # Get project info
        project = session.execute(
            select(Project).where(Project.id == project_id)
        ).scalar_one_or_none()

        # Count blocks (messages)
        block_count = session.execute(
            select(func.count(ProjectBlock.id)).where(ProjectBlock.project_id == project_id)
        ).scalar() or 0

        # Count conversations
        conv_count = session.execute(
            select(func.count(Conversation.id)).where(Conversation.project_id == project_id)
        ).scalar() or 0

        # Query dogme_jobs for this project (shared DB, raw SQL)
        jobs_result = session.execute(text(
            "SELECT run_uuid, sample_name, mode, status, submitted_at, started_at, "
            "completed_at, nextflow_work_dir, remote_work_dir, execution_mode, user_id "
            "FROM dogme_jobs WHERE project_id = :pid ORDER BY submitted_at DESC"
        ), {"pid": project_id}).fetchall()

        jobs = []
        total_completed = 0
        total_failed = 0
        total_running = 0
        for row in jobs_result:
            status = row[3] or "UNKNOWN"
            if status == "COMPLETED":
                total_completed += 1
            elif status == "FAILED":
                total_failed += 1
            elif status == "RUNNING":
                total_running += 1

            submitted_at = row[4]
            started_at = row[5]
            completed_at = row[6]
            work_dir = row[7] or row[8]
            duration_seconds = None
            start_dt = started_at or submitted_at
            end_dt = completed_at or datetime.datetime.utcnow()
            if start_dt:
                try:
                    duration_seconds = max(0, int((end_dt - start_dt).total_seconds()))
                except Exception:
                    duration_seconds = None

            workflow_label = None
            if work_dir:
                try:
                    workflow_label = Path(work_dir).name
                except Exception:
                    workflow_label = None

            jobs.append({
                "run_uuid": row[0],
                "sample_name": row[1],
                "mode": row[2],
                "status": status,
                "submitted_at": str(submitted_at) if submitted_at else None,
                "started_at": str(started_at) if started_at else None,
                "completed_at": str(completed_at) if completed_at else None,
                "duration_seconds": duration_seconds,
                "work_dir": work_dir,
                "workflow_label": workflow_label,
                "execution_mode": row[9],
            })

        # Calculate disk usage — scan actual work directories from dogme_jobs
        # (handles both jailed paths and legacy launchpad_work paths)
        disk_bytes = 0
        file_count = 0
        scanned_dirs = set()

        # 1. Check user project directory (slug-based or legacy UUID)
        project_dir = _dbh._resolve_project_dir(session, user, project_id)
        if project_dir.exists():
            scanned_dirs.add(str(project_dir))
            for f in project_dir.rglob("*"):
                if f.is_file():
                    try:
                        disk_bytes += f.stat().st_size
                        file_count += 1
                    except OSError:
                        pass

        # 2. Also scan work directories recorded in dogme_jobs (covers legacy paths)
        for job in jobs:
            wdir = job.get("work_dir")
            if wdir and wdir not in scanned_dirs:
                wpath = Path(wdir)
                if wpath.exists():
                    scanned_dirs.add(wdir)
                    for f in wpath.rglob("*"):
                        if f.is_file():
                            try:
                                disk_bytes += f.stat().st_size
                                file_count += 1
                            except OSError:
                                pass

        # Token usage for this project
        token_row = session.execute(
            text("""
                SELECT
                    COALESCE(SUM(cm.prompt_tokens), 0)     AS prompt_tokens,
                    COALESCE(SUM(cm.completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(cm.total_tokens), 0)      AS total_tokens
                FROM conversation_messages cm
                JOIN conversations c ON cm.conversation_id = c.id
                WHERE c.project_id = :pid
                  AND cm.role = 'assistant'
                  AND cm.total_tokens IS NOT NULL
            """),
            {"pid": project_id}
        ).fetchone()

        return {
            "project_id": project_id,
            "name": project.name if project else project_id,
            "is_archived": project.is_archived if project else False,
            "created_at": str(project.created_at) if project else None,
            "message_count": block_count,
            "conversation_count": conv_count,
            "jobs": jobs,
            "job_count": len(jobs),
            "completed_count": total_completed,
            "failed_count": total_failed,
            "running_count": total_running,
            "disk_usage_bytes": disk_bytes,
            "disk_usage_mb": round(disk_bytes / (1024 * 1024), 2),
            "file_count": file_count,
            "token_usage": {
                "prompt_tokens": token_row[0] if token_row else 0,
                "completion_tokens": token_row[1] if token_row else 0,
                "total_tokens": token_row[2] if token_row else 0,
            },
        }
    finally:
        session.close()


@router.get("/projects/{project_id}/files")
async def list_project_files(project_id: str, request: Request):
    """List all files in a project — checks both jailed dir and legacy work dirs."""
    user = request.state.user
    require_project_access(project_id, user, min_role="viewer")

    files = []
    total_size = 0
    scanned_dirs = set()

    def _scan_dir(base_dir: Path, label: str = ""):
        nonlocal total_size
        if not base_dir.exists() or str(base_dir) in scanned_dirs:
            return
        scanned_dirs.add(str(base_dir))
        for f in sorted(base_dir.rglob("*")):
            if f.is_file():
                try:
                    size = f.stat().st_size
                    total_size += size
                    files.append({
                        "path": str(f.relative_to(base_dir)),
                        "name": f.name,
                        "size_bytes": size,
                        "extension": f.suffix,
                        "modified": str(datetime.datetime.fromtimestamp(f.stat().st_mtime)),
                        "source": label,
                    })
                except OSError:
                    pass

    # 1. User project directory (slug-based or legacy UUID)
    session_files = _db.SessionLocal()
    try:
        project_dir = _dbh._resolve_project_dir(session_files, user, project_id)
    finally:
        session_files.close()
    _scan_dir(project_dir, "project")

    # 2. Legacy work directories from dogme_jobs
    session = _db.SessionLocal()
    try:
        rows = session.execute(
            text("SELECT run_uuid, nextflow_work_dir FROM dogme_jobs WHERE project_id = :pid"),
            {"pid": project_id}
        ).fetchall()
        for row in rows:
            wdir = row[1]
            if wdir:
                _scan_dir(Path(wdir), f"job:{row[0][:8]}")
    except Exception:
        pass
    finally:
        session.close()

    return {
        "files": files,
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "file_count": len(files),
    }


# =============================================================================
# TOKEN & DISK USAGE
# =============================================================================

@router.get("/user/token-usage")
async def get_user_token_usage(request: Request):
    """Return token usage for the authenticated user.

    Response shape:
      {
        "lifetime": {prompt_tokens, completion_tokens, total_tokens},
        "by_conversation": [ {conversation_id, project_id, title, prompt_tokens,
                               completion_tokens, total_tokens, last_message_at} ],
        "daily": [ {date, prompt_tokens, completion_tokens, total_tokens} ],
        "tracking_since": ISO string of earliest tracked message (or null)
      }
    """
    user = request.state.user
    session = _db.SessionLocal()
    try:
        archived_lifetime_row = session.execute(
            text(
                """
                SELECT
                    COALESCE(SUM(prompt_tokens), 0)     AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(total_tokens), 0)      AS total_tokens
                FROM deleted_project_token_usage
                WHERE user_id = :uid
                """
            ),
            {"uid": user.id}
        ).fetchone()

        # Lifetime totals
        lifetime_row = session.execute(
            text("""
                SELECT
                    COALESCE(SUM(cm.prompt_tokens), 0)     AS prompt_tokens,
                    COALESCE(SUM(cm.completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(cm.total_tokens), 0)      AS total_tokens
                FROM conversation_messages cm
                JOIN conversations c ON cm.conversation_id = c.id
                WHERE c.user_id = :uid
                  AND cm.role = 'assistant'
                  AND cm.total_tokens IS NOT NULL
            """),
            {"uid": user.id}
        ).fetchone()

        live_prompt_tokens = int(lifetime_row[0] or 0) if lifetime_row else 0
        live_completion_tokens = int(lifetime_row[1] or 0) if lifetime_row else 0
        live_total_tokens = int(lifetime_row[2] or 0) if lifetime_row else 0
        archived_prompt_tokens = int(archived_lifetime_row[0] or 0) if archived_lifetime_row else 0
        archived_completion_tokens = int(archived_lifetime_row[1] or 0) if archived_lifetime_row else 0
        archived_total_tokens = int(archived_lifetime_row[2] or 0) if archived_lifetime_row else 0

        # Per-conversation totals
        conv_rows = session.execute(
            text("""
                SELECT
                    c.id                                        AS conversation_id,
                    c.project_id,
                    c.title,
                    COALESCE(SUM(cm.prompt_tokens), 0)          AS prompt_tokens,
                    COALESCE(SUM(cm.completion_tokens), 0)      AS completion_tokens,
                    COALESCE(SUM(cm.total_tokens), 0)           AS total_tokens,
                    MAX(cm.created_at)                          AS last_message_at
                FROM conversations c
                JOIN conversation_messages cm ON cm.conversation_id = c.id
                WHERE c.user_id = :uid
                  AND cm.role = 'assistant'
                  AND cm.total_tokens IS NOT NULL
                GROUP BY c.id, c.project_id, c.title
                ORDER BY last_message_at DESC
            """),
            {"uid": user.id}
        ).fetchall()

        archived_conv_rows = session.execute(
            text(
                """
                SELECT
                    project_id,
                    project_name,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    deleted_at
                FROM deleted_project_token_usage
                WHERE user_id = :uid
                ORDER BY deleted_at DESC
                """
            ),
            {"uid": user.id}
        ).fetchall()

        # Daily time-series (SQLite date() function)
        daily_rows = session.execute(
            text("""
                SELECT
                    DATE(cm.created_at)                         AS date,
                    COALESCE(SUM(cm.prompt_tokens), 0)          AS prompt_tokens,
                    COALESCE(SUM(cm.completion_tokens), 0)      AS completion_tokens,
                    COALESCE(SUM(cm.total_tokens), 0)           AS total_tokens
                FROM conversation_messages cm
                JOIN conversations c ON cm.conversation_id = c.id
                WHERE c.user_id = :uid
                  AND cm.role = 'assistant'
                  AND cm.total_tokens IS NOT NULL
                GROUP BY DATE(cm.created_at)
                ORDER BY DATE(cm.created_at) ASC
            """),
            {"uid": user.id}
        ).fetchall()

        archived_daily_rows = session.execute(
            text(
                """
                SELECT
                    usage_date,
                    COALESCE(SUM(prompt_tokens), 0)     AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(total_tokens), 0)      AS total_tokens
                FROM deleted_project_token_daily
                WHERE user_id = :uid
                GROUP BY usage_date
                ORDER BY usage_date ASC
                """
            ),
            {"uid": user.id}
        ).fetchall()

        # Earliest tracked message
        tracking_since_row = session.execute(
            text("""
                SELECT MIN(cm.created_at)
                FROM conversation_messages cm
                JOIN conversations c ON cm.conversation_id = c.id
                WHERE c.user_id = :uid
                  AND cm.role = 'assistant'
                  AND cm.total_tokens IS NOT NULL
            """),
            {"uid": user.id}
        ).fetchone()

        archived_tracking_row = session.execute(
            text(
                """
                SELECT MIN(usage_date)
                FROM deleted_project_token_daily
                WHERE user_id = :uid
                """
            ),
            {"uid": user.id}
        ).fetchone()

        by_conversation = [
            {
                "conversation_id": r[0],
                "project_id": r[1],
                "title": r[2],
                "prompt_tokens": r[3],
                "completion_tokens": r[4],
                "total_tokens": r[5],
                "last_message_at": str(r[6]) if r[6] else None,
            }
            for r in conv_rows
        ]
        by_conversation.extend(
            {
                "conversation_id": None,
                "project_id": r[0],
                "title": r[1] or "Deleted project",
                "prompt_tokens": int(r[2] or 0),
                "completion_tokens": int(r[3] or 0),
                "total_tokens": int(r[4] or 0),
                "last_message_at": str(r[5]) if r[5] else None,
            }
            for r in archived_conv_rows
        )
        by_conversation.sort(
            key=lambda row: row["last_message_at"] or "",
            reverse=True,
        )

        daily_map = {}
        for row in daily_rows:
            key = str(row[0])
            daily_map[key] = {
                "date": key,
                "prompt_tokens": int(row[1] or 0),
                "completion_tokens": int(row[2] or 0),
                "total_tokens": int(row[3] or 0),
            }
        for row in archived_daily_rows:
            key = str(row[0])
            entry = daily_map.setdefault(
                key,
                {
                    "date": key,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            )
            entry["prompt_tokens"] += int(row[1] or 0)
            entry["completion_tokens"] += int(row[2] or 0)
            entry["total_tokens"] += int(row[3] or 0)

        tracking_candidates = []
        if tracking_since_row and tracking_since_row[0]:
            tracking_candidates.append(str(tracking_since_row[0]))
        if archived_tracking_row and archived_tracking_row[0]:
            tracking_candidates.append(str(archived_tracking_row[0]))

        return {
            "lifetime": {
                "prompt_tokens": live_prompt_tokens + archived_prompt_tokens,
                "completion_tokens": live_completion_tokens + archived_completion_tokens,
                "total_tokens": live_total_tokens + archived_total_tokens,
            },
            "by_conversation": by_conversation,
            "daily": [daily_map[key] for key in sorted(daily_map.keys())],
            "tracking_since": min(tracking_candidates) if tracking_candidates else None,
            "token_limit": user.token_limit,
        }
    finally:
        session.close()


@router.get("/user/disk-usage")
async def get_user_disk_usage(request: Request):
    """Get total disk usage for the current user across all projects.
    Scans both jailed user directories and legacy job work directories."""
    user = request.state.user

    session = _db.SessionLocal()
    try:
        # Get all project IDs for this user
        access_rows = session.execute(
            select(ProjectAccess.project_id)
            .where(ProjectAccess.user_id == user.id)
        ).all()
        project_ids = [r[0] for r in access_rows]

        project_usage = []
        total_bytes = 0

        for pid in project_ids:
            proj_bytes = 0
            file_count = 0
            scanned_dirs = set()

            # 1. User project directory (slug-based or legacy UUID)
            project_dir = _dbh._resolve_project_dir(session, user, pid)
            if project_dir.exists():
                scanned_dirs.add(str(project_dir))
                for f in project_dir.rglob("*"):
                    if f.is_file():
                        try:
                            proj_bytes += f.stat().st_size
                            file_count += 1
                        except OSError:
                            pass

            # 2. Legacy work dirs from dogme_jobs
            try:
                job_rows = session.execute(
                    text("SELECT nextflow_work_dir FROM dogme_jobs WHERE project_id = :pid"),
                    {"pid": pid}
                ).fetchall()
                for row in job_rows:
                    wdir = row[0]
                    if wdir and wdir not in scanned_dirs:
                        wpath = Path(wdir)
                        if wpath.exists():
                            scanned_dirs.add(wdir)
                            for f in wpath.rglob("*"):
                                if f.is_file():
                                    try:
                                        proj_bytes += f.stat().st_size
                                        file_count += 1
                                    except OSError:
                                        pass
            except Exception:
                pass

            total_bytes += proj_bytes
            project_usage.append({
                "project_id": pid,
                "size_bytes": proj_bytes,
                "size_mb": round(proj_bytes / (1024 * 1024), 2),
                "file_count": file_count,
            })

        return {
            "total_bytes": total_bytes,
            "total_mb": round(total_bytes / (1024 * 1024), 2),
            "projects": project_usage,
        }
    finally:
        session.close()


# =============================================================================
# DELETE ENDPOINTS
# =============================================================================

@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, request: Request):
    """
    Soft-delete a project (archives it). Only the owner can delete.
    Does NOT remove files — use archive to hide from listing.
    """
    user = request.state.user
    require_project_access(project_id, user, min_role="owner")

    session = _db.SessionLocal()
    try:
        project = session.execute(
            select(Project).where(Project.id == project_id)
        ).scalar_one_or_none()

        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        project.is_archived = True
        project.updated_at = datetime.datetime.utcnow()
        session.commit()

        logger.info("Project archived", project_id=project_id, user=user.email)
        return {"status": "archived", "id": project_id}
    finally:
        session.close()


@router.delete("/projects/{project_id}/permanent")
async def delete_project_permanent(project_id: str, request: Request):
    """
    Permanently delete a project and all associated data:
    conversations, messages, blocks, job_results, project_access,
    dogme_jobs rows, and the on-disk directory.
    Requires owner role.
    """
    user = request.state.user
    require_project_access(project_id, user, min_role="owner")

    session = _db.SessionLocal()
    try:
        project = session.execute(
            select(Project).where(Project.id == project_id)
        ).scalar_one_or_none()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        project_dir = _dbh._resolve_project_dir(session, user, project_id)

        _dbh.archive_deleted_project_token_usage(
            session,
            project_id=project_id,
            user_id=user.id,
            project_name=project.name,
        )

        # 1. Delete conversation messages (via conversation ids)
        conv_ids = [r[0] for r in session.execute(
            select(Conversation.id).where(Conversation.project_id == project_id)
        ).all()]
        if conv_ids:
            session.execute(
                ConversationMessage.__table__.delete().where(
                    ConversationMessage.conversation_id.in_(conv_ids)
                )
            )
            # Delete job_results linked to those conversations
            from cortex.models import JobResult
            session.execute(
                JobResult.__table__.delete().where(
                    JobResult.conversation_id.in_(conv_ids)
                )
            )

        # 2. Delete conversations
        session.execute(
            Conversation.__table__.delete().where(Conversation.project_id == project_id)
        )

        # 3. Delete project blocks
        session.execute(
            ProjectBlock.__table__.delete().where(ProjectBlock.project_id == project_id)
        )

        # 4. Delete dogme_jobs rows (cross-server table)
        try:
            session.execute(
                text("DELETE FROM dogme_jobs WHERE project_id = :pid"),
                {"pid": project_id}
            )
        except Exception:
            pass  # Table may not exist yet

        # 5. Delete project_access records
        session.execute(
            ProjectAccess.__table__.delete().where(ProjectAccess.project_id == project_id)
        )

        # 6. Delete the project record itself
        session.execute(
            Project.__table__.delete().where(Project.id == project_id)
        )

        session.commit()

        # 7. Remove on-disk directory (best-effort)
        import shutil
        if project_dir.exists():
            shutil.rmtree(project_dir, ignore_errors=True)
        # Also try legacy UUID path
        legacy_dir = _cfg.AGOUTIC_DATA / "users" / str(user.id) / project_id
        if legacy_dir.exists():
            shutil.rmtree(legacy_dir, ignore_errors=True)

        logger.info("Project permanently deleted", project_id=project_id, user=user.email)
        return {"status": "deleted", "id": project_id}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error("Failed to delete project", project_id=project_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")
    finally:
        session.close()
