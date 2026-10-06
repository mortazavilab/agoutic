import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

import cortex.routes.literature as literature_routes
from cortex.models import SavedLiteratureSearch


def _request(user_id: str):
    return SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=user_id)))


def test_saved_searches_are_scoped_to_the_authenticated_user(
    db_engine, monkeypatch
):
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    monkeypatch.setattr(literature_routes, "SessionLocal", factory)
    request = _request("user-a")
    body = literature_routes.SavedSearchRequest(
        name="Mouse kidneys",
        query="mouse kidney",
        known_pmids=["1", "2"],
    )

    saved = literature_routes.save_literature_search(body, request)

    assert saved["name"] == "Mouse kidneys"
    assert saved["filters"]["result_count"] == 10
    assert [item["id"] for item in literature_routes.list_saved_searches(request)] == [saved["id"]]
    assert literature_routes.list_saved_searches(_request("user-b")) == []
    with pytest.raises(HTTPException) as exc:
        literature_routes.delete_literature_search(saved["id"], _request("user-b"))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_search_route_passes_validated_filters_and_authenticated_user(monkeypatch):
    observed = {}

    async def fake_search(query, options, user_id):
        observed.update(query=query, options=options.model_dump(), user_id=user_id)
        return {"query": query, "papers": []}

    monkeypatch.setattr(literature_routes, "_search", fake_search)
    request = literature_routes.LiteratureSearchRequest(
        query="mouse kidney",
        result_count=15,
        year_from=2010,
        year_to=2020,
        organism="mouse",
        study_design="single-cell study",
        sort_by="recency",
    )

    result = await literature_routes.search_literature(request, _request("user-a"))

    assert result["query"] == "mouse kidney"
    assert observed["user_id"] == "user-a"
    assert observed["options"]["year_from"] == 2010
    assert observed["options"]["organism"] == "mouse"
    assert observed["options"]["study_design"] == "single-cell study"
    assert observed["options"]["sort_by"] == "recency"


@pytest.mark.asyncio
async def test_saved_search_check_reports_and_snapshots_new_pmids(db_engine, monkeypatch):
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    monkeypatch.setattr(literature_routes, "SessionLocal", factory)
    request = _request("user-a")
    saved = literature_routes.save_literature_search(
        literature_routes.SavedSearchRequest(
            name="Mouse kidneys",
            query="mouse kidney",
            known_pmids=["1"],
        ),
        request,
    )
    monkeypatch.setattr(
        literature_routes,
        "_search",
        AsyncMock(return_value={
            "papers": [
                {"pmid": "1", "title": "Known paper"},
                {"pmid": "2", "title": "New paper"},
            ]
        }),
    )

    result = await literature_routes.check_saved_literature_search(saved["id"], request)

    assert result["new_count"] == 1
    assert result["new_papers"] == [{"pmid": "2", "title": "New paper"}]
    with factory() as session:
        row = session.get(SavedLiteratureSearch, saved["id"])
        assert json.loads(row.known_pmids_json) == ["1", "2"]
        assert row.last_checked_at is not None
