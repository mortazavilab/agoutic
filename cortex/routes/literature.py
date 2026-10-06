"""Authenticated literature search and saved-search endpoints."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

import common
import cortex.config as _cfg
from cortex.db import SessionLocal
from cortex.models import SavedLiteratureSearch

router = APIRouter(prefix="/literature", tags=["literature"])


class SearchOptions(BaseModel):
    result_count: int = Field(default=10, ge=1, le=20)
    year_from: int | None = Field(default=None, ge=1800, le=2100)
    year_to: int | None = Field(default=None, ge=1800, le=2100)
    organism: str | None = Field(
        default=None,
        max_length=50,
        pattern=r"^[A-Za-z][A-Za-z0-9 -]{0,49}$",
    )
    article_type: Literal[
        "journal article",
        "comparative study",
        "clinical trial",
        "randomized controlled trial",
        "review",
        "systematic review",
        "meta-analysis",
        "case report",
    ] | None = None
    study_design: Literal[
        "functional study",
        "functional genomics",
        "comparative study",
        "comparative genomics",
        "systems genetics",
        "population genetics",
        "population genomics",
        "genome-wide association study",
        "quantitative trait loci study",
        "cohort study",
        "case-control study",
        "cross-sectional study",
        "single-cell study",
        "animal study",
    ] | None = None
    open_access_only: bool = False
    sort_by: Literal["relevance", "recency"] = "relevance"


class LiteratureSearchRequest(SearchOptions):
    query: str = Field(min_length=1, max_length=500)


class SavedSearchRequest(LiteratureSearchRequest):
    name: str = Field(min_length=1, max_length=120)
    known_pmids: list[str] = Field(default_factory=list, max_length=20)
    alert_enabled: bool = True


async def _search(query: str, options: SearchOptions, user_id: str) -> dict:
    client = common.MCPHttpClient(
        name="literature",
        base_url=_cfg.get_service_url("literature"),
    )
    try:
        await client.connect()
        result = await client.call_tool(
            "search_literature",
            query=query,
            user_id=user_id,
            **options.model_dump(exclude_none=True),
        )
        if not isinstance(result, dict) or not isinstance(result.get("papers"), list):
            raise RuntimeError("Literature service returned an invalid result payload.")
        return result
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Literature search failed: {exc}") from exc
    finally:
        await client.disconnect()


def _saved_search_out(row: SavedLiteratureSearch) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "query": row.query,
        "filters": json.loads(row.filters_json),
        "alert_enabled": row.alert_enabled,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.post("/search")
async def search_literature(body: LiteratureSearchRequest, request: Request) -> dict:
    if not body.query.strip():
        raise HTTPException(status_code=422, detail="A literature search query is required.")
    if body.year_from and body.year_to and body.year_from > body.year_to:
        raise HTTPException(status_code=422, detail="year_from cannot be later than year_to.")
    options = SearchOptions(**body.model_dump(exclude={"query"}))
    try:
        return await _search(body.query.strip(), options, request.state.user.id)
    except HTTPException as exc:
        if exc.status_code == 502:
            raise
        raise HTTPException(status_code=422, detail=exc.detail) from exc


@router.get("/saved-searches")
def list_saved_searches(request: Request) -> list[dict]:
    with SessionLocal() as session:
        rows = session.execute(
            select(SavedLiteratureSearch)
            .where(SavedLiteratureSearch.user_id == request.state.user.id)
            .order_by(SavedLiteratureSearch.created_at.desc())
        ).scalars().all()
        return [_saved_search_out(row) for row in rows]


@router.post("/saved-searches")
def save_literature_search(body: SavedSearchRequest, request: Request) -> dict:
    if not body.query.strip():
        raise HTTPException(status_code=422, detail="A literature search query is required.")
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="A saved search name is required.")
    if body.year_from and body.year_to and body.year_from > body.year_to:
        raise HTTPException(status_code=422, detail="year_from cannot be later than year_to.")
    options = SearchOptions(**body.model_dump(exclude={"name", "query", "known_pmids", "alert_enabled"}))
    row = SavedLiteratureSearch(
        id=str(uuid.uuid4()),
        user_id=request.state.user.id,
        name=body.name.strip(),
        query=body.query.strip(),
        filters_json=json.dumps(options.model_dump(exclude_none=True)),
        known_pmids_json=json.dumps(list(dict.fromkeys(body.known_pmids))),
        alert_enabled=body.alert_enabled,
    )
    with SessionLocal() as session:
        session.add(row)
        session.commit()
        session.refresh(row)
        return _saved_search_out(row)


@router.delete("/saved-searches/{search_id}")
def delete_literature_search(search_id: str, request: Request) -> dict:
    with SessionLocal() as session:
        row = session.execute(
            select(SavedLiteratureSearch).where(
                SavedLiteratureSearch.id == search_id,
                SavedLiteratureSearch.user_id == request.state.user.id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Saved search not found.")
        session.delete(row)
        session.commit()
    return {"deleted": True}


@router.post("/saved-searches/{search_id}/check")
async def check_saved_literature_search(search_id: str, request: Request) -> dict:
    with SessionLocal() as session:
        row = session.execute(
            select(SavedLiteratureSearch).where(
                SavedLiteratureSearch.id == search_id,
                SavedLiteratureSearch.user_id == request.state.user.id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Saved search not found.")
        if not row.alert_enabled:
            raise HTTPException(status_code=409, detail="New-paper checks are disabled for this search.")
        search_id_value = row.id
        query = row.query
        options = SearchOptions(**json.loads(row.filters_json))
        known_pmids = set(json.loads(row.known_pmids_json))

    result = await _search(query, options, request.state.user.id)
    papers = result.get("papers", [])
    new_papers = [paper for paper in papers if str(paper.get("pmid") or "") not in known_pmids]

    with SessionLocal() as session:
        row = session.execute(
            select(SavedLiteratureSearch).where(
                SavedLiteratureSearch.id == search_id_value,
                SavedLiteratureSearch.user_id == request.state.user.id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Saved search not found.")
        row.known_pmids_json = json.dumps([
            str(paper.get("pmid")) for paper in papers if paper.get("pmid")
        ])
        row.last_checked_at = datetime.now(timezone.utc)
        session.commit()
    return {
        "search_id": search_id_value,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "new_count": len(new_papers),
        "new_papers": new_papers,
    }
