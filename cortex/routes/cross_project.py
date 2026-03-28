"""
Cross-project browser and staging workflow.

Provides endpoints for:
1. Listing authorized projects
2. Browsing files within authorized projects
3. Searching across authorized projects
4. Staging selected files into a destination workspace
5. Retrieving staging status

Security constraints:
- Authorization filtering happens BEFORE any filesystem scan
- Absolute paths remain server-internal, never exposed in API responses
- All path operations use existing jail utilities
- Cross-project work stages first, no auto-analysis
"""

import datetime
import json
import os
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select, desc

import cortex.config as _cfg
import cortex.db as _db
from cortex.dependencies import require_project_access
from cortex.db_helpers import _resolve_project_dir
from cortex.models import Project, ProjectAccess, User
from cortex.schemas import (
    CrossProjectProjectOut,
    CrossProjectFileOut,
    CrossProjectBrowseResponse,
    CrossProjectSearchResponse,
    LogicalFileReference,
    SelectedFileInput,
    StageRequest,
    StageResponse,
    StageStatusResponse,
)
from cortex.user_jail import (
    _ensure_within_jail,
    get_user_project_dir,
)
from cortex.routes.projects import _slugify, _dedup_slug
from common.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/cross-project", tags=["cross-project"])


# ---------------------------------------------------------------------------
# Helper constants
# ---------------------------------------------------------------------------

# File type classification based on extension
_FILE_TYPE_MAP = {
    ".bam": "alignment",
    ".bai": "alignment_index",
    ".fastq": "sequence",
    ".fastq.gz": "sequence",
    ".fq": "sequence",
    ".fq.gz": "sequence",
    ".fa": "reference",
    ".fasta": "reference",
    ".fa.gz": "reference",
    ".fasta.gz": "reference",
    ".gtf": "annotation",
    ".gtf.gz": "annotation",
    ".gff": "annotation",
    ".gff3": "annotation",
    ".bed": "region",
    ".vcf": "variant",
    ".vcf.gz": "variant",
    ".pod5": "raw_signal",
    ".tsv": "tabular",
    ".csv": "tabular",
    ".json": "metadata",
    ".html": "report",
    ".pdf": "report",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".log": "log",
    ".txt": "text",
}

# Workflow folder patterns
_WORKFLOW_FOLDER_PATTERN = re.compile(r'^workflow\d+$')


def _classify_file_type(filename: str) -> str:
    """Classify file type based on extension."""
    name_lower = filename.lower()
    # Check compound extensions first
    for ext, ftype in _FILE_TYPE_MAP.items():
        if name_lower.endswith(ext):
            return ftype
    return "other"


def _detect_workflow_folder(relative_path: str) -> Optional[str]:
    """Extract workflow folder name if path is under a workflow directory."""
    parts = Path(relative_path).parts
    for part in parts:
        if _WORKFLOW_FOLDER_PATTERN.match(part):
            return part
    return None


def _detect_category(relative_path: str) -> str:
    """Detect category based on path structure."""
    parts = Path(relative_path).parts
    if "data" in parts:
        return "data"
    if any(_WORKFLOW_FOLDER_PATTERN.match(p) for p in parts):
        return "workflow_output"
    if "results" in parts:
        return "results"
    return "general"


def _safe_relative_path(subpath: str) -> str:
    """
    Validate and normalize a subpath to prevent traversal attacks.
    Returns the normalized path or raises HTTPException.
    """
    if not subpath:
        return ""

    # Reject absolute paths
    if subpath.startswith("/") or subpath.startswith("\\"):
        raise HTTPException(
            status_code=400,
            detail="Absolute paths not allowed in subpath"
        )

    # Reject traversal attempts
    if ".." in subpath:
        raise HTTPException(
            status_code=400,
            detail="Path traversal not allowed"
        )

    # Reject invalid path tokens for user-controlled references
    if any(tok in subpath for tok in ["*", "?", "|", "<", ">"]):
        raise HTTPException(
            status_code=400,
            detail="Invalid path tokens"
        )

    # Reject Windows drive-style absolute paths (e.g., C:\\foo)
    if re.match(r'^[a-zA-Z]:', subpath):
        raise HTTPException(
            status_code=400,
            detail="Absolute paths not allowed in subpath"
        )

    # Reject null bytes
    if "\x00" in subpath:
        raise HTTPException(
            status_code=400,
            detail="Invalid characters in path"
        )

    # Normalize the path (removes redundant separators, etc.)
    normalized = os.path.normpath(subpath)

    # Double-check after normalization
    if normalized.startswith("..") or normalized.startswith("/"):
        raise HTTPException(
            status_code=400,
            detail="Path traversal not allowed"
        )

    return normalized


def _resolve_relative_path_from_logical_reference(
    project_dir: Path,
    logical_reference: LogicalFileReference,
) -> str:
    """Resolve a logical file reference to exactly one project-relative file path."""
    if logical_reference is None:
        raise HTTPException(status_code=400, detail="Missing logical reference")

    if not any([
        logical_reference.workflow_name,
        logical_reference.sample_name,
        logical_reference.file_type,
    ]):
        raise HTTPException(
            status_code=400,
            detail="Logical reference must include at least one of workflow_name, sample_name, or file_type",
        )

    matches: list[str] = []
    workflow_name = (logical_reference.workflow_name or "").strip().lower()
    sample_name = (logical_reference.sample_name or "").strip().lower()
    file_type = (logical_reference.file_type or "").strip().lower()

    for path in project_dir.rglob("*"):
        if not path.is_file():
            continue

        rel_path = str(path.relative_to(project_dir))
        rel_parts = [p.lower() for p in Path(rel_path).parts]
        name_lower = path.name.lower()
        derived_type = _classify_file_type(path.name).lower()

        if workflow_name and workflow_name not in rel_parts:
            continue
        if sample_name and sample_name not in name_lower:
            continue
        if file_type and file_type not in {derived_type, path.suffix.lower().lstrip('.')}:
            continue

        matches.append(rel_path)

    if not matches:
        raise HTTPException(status_code=422, detail="No files matched logical reference")

    if len(matches) > 1:
        preview = ", ".join(matches[:5])
        raise HTTPException(
            status_code=422,
            detail=f"Logical reference is ambiguous ({len(matches)} matches): {preview}",
        )

    return _safe_relative_path(matches[0])


# ---------------------------------------------------------------------------
# Phase 1: Browser Endpoints
# ---------------------------------------------------------------------------

@router.get("/projects", response_model=list[CrossProjectProjectOut])
async def list_authorized_projects(request: Request):
    """
    Return only the projects the current user is authorized to access.

    No filesystem traversal here - just returns lightweight project metadata
    from the database after authorization filtering.
    """
    user = request.state.user

    session = _db.SessionLocal()
    try:
        # Get all project_access records for this user
        # Authorization filtering happens AT QUERY TIME - we only fetch
        # projects this user has explicit access to
        query = select(ProjectAccess)\
            .where(ProjectAccess.user_id == user.id)\
            .order_by(desc(ProjectAccess.last_accessed))

        result = session.execute(query)
        access_records = result.scalars().all()

        projects = []
        for acc in access_records:
            # Get the full Project record for additional metadata
            proj = session.execute(
                select(Project).where(Project.id == acc.project_id)
            ).scalar_one_or_none()

            projects.append(CrossProjectProjectOut(
                project_id=acc.project_id,
                project_name=proj.name if proj else acc.project_name,
                slug=proj.slug if proj else None,
                role=acc.role if hasattr(acc, 'role') else "owner",
                is_archived=proj.is_archived if proj else False,
                is_public=proj.is_public if proj else False,
                created_at=str(proj.created_at) if proj and proj.created_at else None,
                updated_at=str(proj.updated_at) if proj and proj.updated_at else None,
            ))

        return projects
    finally:
        session.close()


@router.get("/projects/{project_id}/browse", response_model=CrossProjectBrowseResponse)
async def browse_project(
    project_id: str,
    request: Request,
    subpath: str = Query("", description="Relative path within the project"),
    recursive: bool = Query(False, description="Recursively list files"),
    limit: int = Query(500, ge=1, le=2000, description="Max items to return"),
    total_count_full_scan: bool = Query(False, description="If true, keep scanning after limit to compute exact total_count"),
    category: Optional[str] = Query(None, description="Filter by category"),
):
    """
    Browse one accessible project by folder/subpath.

    Security:
    - Check access to the specific project first
    - Normalize and validate the subpath
    - Reject traversal and absolute-path escape attempts
    - Resolve from the project root server-side
    - Return only safe metadata (no absolute paths)
    """
    user = request.state.user

    # AUTHORIZATION FIRST - before any filesystem access
    require_project_access(project_id, user, min_role="viewer")

    # Validate and normalize subpath
    safe_subpath = _safe_relative_path(subpath)

    session = _db.SessionLocal()
    try:
        # Get project info
        project = session.execute(
            select(Project).where(Project.id == project_id)
        ).scalar_one_or_none()

        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        # Resolve project directory (server-side only)
        project_dir = _resolve_project_dir(session, user, project_id)

        # Resolve target directory
        if safe_subpath:
            target_dir = project_dir / safe_subpath
        else:
            target_dir = project_dir

        # Ensure target is still within project directory (belt-and-suspenders)
        try:
            target_dir.resolve().relative_to(project_dir.resolve())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Path traversal detected"
            )

        if not target_dir.exists():
            return CrossProjectBrowseResponse(
                project_id=project_id,
                project_name=project.name,
                subpath=safe_subpath,
                items=[],
                total_count=0,
                truncated=False,
                limit=limit,
            )

        # Collect items
        items = []
        total_count = 0
        early_stopped = False

        if recursive:
            iterator = target_dir.rglob("*")
        else:
            iterator = target_dir.iterdir()

        for path in iterator:
            # Build relative path from project root
            try:
                rel_path = str(path.relative_to(project_dir))
            except ValueError:
                continue  # Skip if somehow outside project dir

            is_dir = path.is_dir()
            file_type = "directory" if is_dir else _classify_file_type(path.name)
            cat = _detect_category(rel_path)

            # Apply category filter if specified
            if category and cat != category:
                continue

            # For recursive scans, stop early at limit unless full-scan is requested.
            if recursive and not total_count_full_scan and len(items) >= limit:
                early_stopped = True
                break

            total_count += 1

            # Respect limit
            if len(items) >= limit:
                continue  # Keep counting but don't add more items

            # Get file metadata
            size = None
            modified_time = None
            if not is_dir:
                try:
                    stat = path.stat()
                    size = stat.st_size
                    modified_time = datetime.datetime.fromtimestamp(
                        stat.st_mtime
                    ).isoformat()
                except OSError:
                    pass

            items.append(CrossProjectFileOut(
                project_id=project_id,
                project_name=project.name,
                relative_path=rel_path,
                name=path.name,
                is_dir=is_dir,
                category=cat,
                workflow_folder=_detect_workflow_folder(rel_path),
                file_type=file_type,
                size=size,
                modified_time=modified_time,
            ))

        return CrossProjectBrowseResponse(
            project_id=project_id,
            project_name=project.name,
            subpath=safe_subpath,
            items=items,
            total_count=total_count,
            truncated=early_stopped or total_count > limit,
            limit=limit,
        )
    finally:
        session.close()


@router.get("/search", response_model=CrossProjectSearchResponse)
async def search_across_projects(
    request: Request,
    q: str = Query(..., min_length=1, description="Search term (filename pattern)"),
    project_ids: Optional[str] = Query(None, description="Comma-separated project IDs to filter"),
    file_type: Optional[str] = Query(None, description="Filter by file type"),
    workflow_folder: Optional[str] = Query(None, description="Filter by workflow folder"),
    limit: int = Query(500, ge=1, le=2000, description="Max items to return"),
    total_count_full_scan: bool = Query(False, description="If true, keep scanning after limit to compute exact total_count"),
):
    """
    Search across accessible projects.

    Security:
    - Authorization filtering happens BEFORE filesystem scan
    - Only searches within authorized project roots
    """
    user = request.state.user

    session = _db.SessionLocal()
    try:
        # AUTHORIZATION FIRST - get all authorized projects
        query = select(ProjectAccess).where(ProjectAccess.user_id == user.id)
        result = session.execute(query)
        access_records = result.scalars().all()

        # Filter to requested project IDs if specified
        authorized_project_ids = {acc.project_id for acc in access_records}

        if project_ids:
            requested_ids = set(p.strip() for p in project_ids.split(",") if p.strip())
            # Only search in projects that are BOTH requested AND authorized
            search_project_ids = authorized_project_ids & requested_ids
        else:
            search_project_ids = authorized_project_ids

        if not search_project_ids:
            return CrossProjectSearchResponse(
                query=q,
                items=[],
                total_count=0,
                truncated=False,
                limit=limit,
            )

        # Build project info map
        project_info = {}
        for acc in access_records:
            if acc.project_id in search_project_ids:
                proj = session.execute(
                    select(Project).where(Project.id == acc.project_id)
                ).scalar_one_or_none()
                if proj:
                    project_info[acc.project_id] = {
                        "name": proj.name,
                        "project": proj,
                    }

        # Search each authorized project
        items = []
        total_count = 0
        search_pattern = q.lower()
        early_stopped = False

        for pid in search_project_ids:
            if pid not in project_info:
                continue

            pinfo = project_info[pid]
            proj = pinfo["project"]

            # Resolve project directory
            project_dir = _resolve_project_dir(session, user, pid)

            if not project_dir.exists():
                continue

            # Search files (recursive)
            for path in project_dir.rglob("*"):
                if path.is_dir():
                    continue

                # Check filename match
                if search_pattern not in path.name.lower():
                    continue

                # Build relative path
                try:
                    rel_path = str(path.relative_to(project_dir))
                except ValueError:
                    continue

                ftype = _classify_file_type(path.name)
                cat = _detect_category(rel_path)
                wf_folder = _detect_workflow_folder(rel_path)

                # Apply filters
                if file_type and ftype != file_type:
                    continue
                if workflow_folder and wf_folder != workflow_folder:
                    continue

                if len(items) >= limit and not total_count_full_scan:
                    early_stopped = True
                    break

                total_count += 1

                if len(items) >= limit:
                    continue

                # Get file metadata
                size = None
                modified_time = None
                try:
                    stat = path.stat()
                    size = stat.st_size
                    modified_time = datetime.datetime.fromtimestamp(
                        stat.st_mtime
                    ).isoformat()
                except OSError:
                    pass

                items.append(CrossProjectFileOut(
                    project_id=pid,
                    project_name=pinfo["name"],
                    relative_path=rel_path,
                    name=path.name,
                    is_dir=False,
                    category=cat,
                    workflow_folder=wf_folder,
                    file_type=ftype,
                    size=size,
                    modified_time=modified_time,
                ))

            if early_stopped and not total_count_full_scan:
                break

        return CrossProjectSearchResponse(
            query=q,
            items=items,
            total_count=total_count,
            truncated=early_stopped or total_count > limit,
            limit=limit,
        )
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Phase 2: Staging Endpoints
# ---------------------------------------------------------------------------

def _validate_file_selection(
    session,
    user: User,
    selected_file: SelectedFileInput,
) -> tuple[Path, dict]:
    """
    Validate a single selected file server-side.

    Returns:
        Tuple of (absolute_path, metadata_dict)

    Raises:
        HTTPException if validation fails
    """
    # Verify source project access
    try:
        require_project_access(selected_file.source_project_id, user, min_role="viewer")
    except HTTPException:
        raise HTTPException(
            status_code=403,
            detail=f"No access to project {selected_file.source_project_id}"
        )

    # Get project info
    project = session.execute(
        select(Project).where(Project.id == selected_file.source_project_id)
    ).scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=404,
            detail=f"Project {selected_file.source_project_id} not found"
        )

    if selected_file.logical_reference and selected_file.logical_reference.project_name:
        if selected_file.logical_reference.project_name != project.name:
            raise HTTPException(
                status_code=422,
                detail="logical_reference.project_name does not match source project",
            )

    # Validate/resolve relative path
    if selected_file.relative_path:
        safe_path = _safe_relative_path(selected_file.relative_path)
    elif selected_file.logical_reference:
        project_dir = _resolve_project_dir(session, user, selected_file.source_project_id)
        safe_path = _resolve_relative_path_from_logical_reference(
            project_dir,
            selected_file.logical_reference,
        )
    else:
        raise HTTPException(
            status_code=400,
            detail="Must provide either relative_path or logical_reference"
        )

    if not safe_path:
        raise HTTPException(
            status_code=400,
            detail="Empty relative path not allowed"
        )

    # Resolve absolute path (server-side)
    project_dir = _resolve_project_dir(session, user, selected_file.source_project_id)
    abs_path = project_dir / safe_path

    # Verify it's within the project directory
    try:
        abs_path.resolve().relative_to(project_dir.resolve())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Path traversal detected in {selected_file.relative_path}"
        )

    # Verify file exists and is a file (not directory)
    if not abs_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"File not found: {selected_file.relative_path}"
        )

    if abs_path.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Directories not allowed: {selected_file.relative_path}"
        )

    # Re-derive metadata server-side (don't trust client)
    try:
        stat = abs_path.stat()
        size = stat.st_size
        modified_time = datetime.datetime.fromtimestamp(stat.st_mtime).isoformat()
    except OSError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Cannot read file metadata: {e}"
        )

    metadata = {
        "source_project_id": selected_file.source_project_id,
        "source_project_name": project.name,
        "relative_path": safe_path,
        "category": _detect_category(safe_path),
        "file_type": _classify_file_type(abs_path.name),
        "size": size,
        "modified_time": modified_time,
    }

    return abs_path, metadata


def _validate_action_compatibility(
    action_type: str,
    file_metadata: list[dict],
) -> None:
    """
    Validate that selected files are compatible for the requested action.

    Raises:
        HTTPException with actionable error message if incompatible
    """
    if not file_metadata:
        raise HTTPException(
            status_code=400,
            detail="No files selected for staging"
        )

    if action_type == "stage_workspace":
        # Any files can be staged together
        return

    # For analyze_together and compare_together, validate compatibility
    file_types = {m["file_type"] for m in file_metadata}

    # Remove 'other' as it's a catch-all
    analysis_types = file_types - {"other"}

    if action_type == "compare_together":
        # Comparison requires files of the same type
        if len(analysis_types) > 1:
            raise HTTPException(
                status_code=422,
                detail=f"Cannot compare mixed file types: {', '.join(sorted(analysis_types))}. "
                       f"Select files of the same type for comparison."
            )

        if not analysis_types:
            raise HTTPException(
                status_code=422,
                detail="Cannot compare files of unknown type. "
                       "Select recognized file types (e.g., BAM, FASTQ, VCF)."
            )

    elif action_type == "analyze_together":
        # Analysis should have at least some recognized types
        recognized_analysis_types = {"alignment", "sequence", "variant", "tabular"}
        analyzable = analysis_types & recognized_analysis_types

        if not analyzable and analysis_types:
            raise HTTPException(
                status_code=422,
                detail=f"Selected file types ({', '.join(sorted(analysis_types))}) "
                       f"are not typically analyzed together. "
                       f"Consider using 'stage_workspace' instead."
            )


@router.post("/stage", response_model=StageResponse)
async def stage_files(
    body: StageRequest,
    request: Request,
):
    """
    Stage selected files from multiple projects into a destination workspace.

    This endpoint:
    - Accepts a list of selected files from multiple projects
    - Revalidates every selected item server-side
    - Requires explicit destination context
    - Creates a deterministic staging layout
    - Writes a provenance manifest
    - Does NOT auto-run analysis

    Staging layout:
        selected/
            manifest.json
            by_project/<project_name>/...
    """
    user = request.state.user

    # Validate destination is specified
    if not body.destination_project_id and not body.destination_project_name:
        raise HTTPException(
            status_code=400,
            detail="Must specify either destination_project_id or destination_project_name"
        )

    session = _db.SessionLocal()
    try:
        # Step 1: Revalidate all selected files
        validated_files = []
        for sel in body.selected_files:
            abs_path, metadata = _validate_file_selection(session, user, sel)
            validated_files.append({
                "abs_path": abs_path,
                "metadata": metadata,
            })

        # Step 2: Validate action compatibility
        _validate_action_compatibility(
            body.action_type,
            [vf["metadata"] for vf in validated_files],
        )

        # Step 3: Resolve or create destination project
        if body.destination_project_id:
            # Use existing project - verify access
            try:
                require_project_access(body.destination_project_id, user, min_role="editor")
            except HTTPException:
                raise HTTPException(
                    status_code=403,
                    detail="No editor access to destination project"
                )

            dest_project = session.execute(
                select(Project).where(Project.id == body.destination_project_id)
            ).scalar_one_or_none()

            if not dest_project:
                raise HTTPException(
                    status_code=404,
                    detail="Destination project not found"
                )

            dest_project_id = dest_project.id
            dest_project_name = dest_project.name
        else:
            # Create new destination project
            if not user.username:
                raise HTTPException(
                    status_code=400,
                    detail="User must have a username to create projects"
                )

            new_name = body.destination_project_name
            slug = _slugify(new_name)
            slug = _dedup_slug(session, user.id, slug)

            now = datetime.datetime.utcnow()
            dest_project_id = str(uuid.uuid4())
            dest_project = Project(
                id=dest_project_id,
                name=new_name,
                slug=slug,
                owner_id=user.id,
                is_public=False,
                is_archived=False,
                created_at=now,
                updated_at=now,
            )
            session.add(dest_project)

            access = ProjectAccess(
                id=str(uuid.uuid4()),
                user_id=user.id,
                project_id=dest_project_id,
                project_name=new_name,
                role="owner",
                last_accessed=now,
            )
            session.add(access)

            dest_project_name = new_name

            # Create filesystem directory
            get_user_project_dir(user.username, slug)

        # Step 4: Create staging layout
        stage_id = str(uuid.uuid4())[:8]
        dest_project_dir = _resolve_project_dir(session, user, dest_project_id)
        staging_root = dest_project_dir / "selected"
        staging_root.mkdir(parents=True, exist_ok=True)

        by_project_dir = staging_root / "by_project"
        by_project_dir.mkdir(exist_ok=True)

        # Create symlinks organized by source project
        symlinked_files = []
        for vf in validated_files:
            abs_path = vf["abs_path"]
            metadata = vf["metadata"]

            # Create project subdirectory (sanitized name)
            proj_name_safe = re.sub(r'[^\w\s-]', '', metadata["source_project_name"])
            proj_name_safe = re.sub(r'[-\s]+', '_', proj_name_safe).strip('_').lower()
            if not proj_name_safe:
                proj_name_safe = "project"

            proj_dir = by_project_dir / proj_name_safe
            proj_dir.mkdir(exist_ok=True)

            # Preserve subdirectory structure from source
            rel_path = Path(metadata["relative_path"])
            if len(rel_path.parts) > 1:
                # Has subdirectories
                subdir = proj_dir / rel_path.parent
                subdir.mkdir(parents=True, exist_ok=True)
                symlink_path = subdir / rel_path.name
            else:
                symlink_path = proj_dir / rel_path.name

            # Create symlink (source files remain unchanged)
            if symlink_path.exists() or symlink_path.is_symlink():
                symlink_path.unlink()

            symlink_path.symlink_to(abs_path.resolve())

            # Record for manifest
            symlinked_files.append({
                "source_project_id": metadata["source_project_id"],
                "source_project_name": metadata["source_project_name"],
                "relative_path": metadata["relative_path"],
                "category": metadata["category"],
                "file_type": metadata["file_type"],
                "size": metadata["size"],
                "modified_time": metadata["modified_time"],
                "staged_path": str(symlink_path.relative_to(dest_project_dir)),
            })

        # Step 5: Write manifest
        created_at = datetime.datetime.utcnow().isoformat()
        manifest = {
            "action_type": body.action_type,
            "created_at": created_at,
            "destination_project_id": dest_project_id,
            "destination_project_name": dest_project_name,
            "staging_mode": "symlink",
            "stage_id": stage_id,
            "selected_files": symlinked_files,
        }

        manifest_path = staging_root / "manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        session.commit()

        logger.info(
            "Cross-project staging completed",
            stage_id=stage_id,
            destination_project_id=dest_project_id,
            action_type=body.action_type,
            file_count=len(validated_files),
            user=user.email,
        )

        return StageResponse(
            stage_id=stage_id,
            destination_project_id=dest_project_id,
            destination_project_name=dest_project_name,
            created_at=created_at,
            action_type=body.action_type,
            staging_mode="symlink",
            item_count=len(validated_files),
            manifest_path="selected/manifest.json",
        )
    except HTTPException:
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        logger.error("Cross-project staging failed", error=str(e), user=user.email)
        raise HTTPException(status_code=500, detail=f"Staging failed: {str(e)}")
    finally:
        session.close()


@router.get("/stage/{stage_id}", response_model=StageStatusResponse)
async def get_stage_status(
    stage_id: str,
    request: Request,
):
    """
    Return metadata/status for a staged workspace.

    Searches for manifest.json files containing the stage_id.
    """
    user = request.state.user

    session = _db.SessionLocal()
    try:
        # Get all authorized projects
        query = select(ProjectAccess).where(ProjectAccess.user_id == user.id)
        result = session.execute(query)
        access_records = result.scalars().all()

        # Search for the manifest in all authorized projects
        for acc in access_records:
            project = session.execute(
                select(Project).where(Project.id == acc.project_id)
            ).scalar_one_or_none()

            if not project:
                continue

            project_dir = _resolve_project_dir(session, user, acc.project_id)
            manifest_path = project_dir / "selected" / "manifest.json"

            if manifest_path.exists():
                try:
                    with open(manifest_path) as f:
                        manifest = json.load(f)

                    if manifest.get("stage_id") == stage_id:
                        return StageStatusResponse(
                            stage_id=stage_id,
                            destination_project_id=manifest["destination_project_id"],
                            destination_project_name=manifest["destination_project_name"],
                            created_at=manifest["created_at"],
                            action_type=manifest["action_type"],
                            staging_mode=manifest["staging_mode"],
                            item_count=len(manifest.get("selected_files", [])),
                            manifest_relative_path="selected/manifest.json",
                        )
                except (json.JSONDecodeError, KeyError):
                    continue

        raise HTTPException(status_code=404, detail=f"Stage {stage_id} not found")
    finally:
        session.close()
