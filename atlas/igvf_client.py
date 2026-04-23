"""
IGVF Portal HTTP Client

Direct httpx client against https://data.igvf.org REST API (Snovault-based).
Handles JSON content negotiation, pagination, and response parsing.
"""

import time
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.data.igvf.org"
DEFAULT_TIMEOUT = 30.0
CACHE_TTL = 300  # 5 minutes


class IGVFClient:
    """Lightweight HTTP client for the IGVF Data Portal REST API."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Accept": "application/json"},
            follow_redirects=True,
        )
        self._cache: dict[str, tuple[float, Any]] = {}

    def _cache_get(self, key: str) -> Any | None:
        if key in self._cache:
            ts, value = self._cache[key]
            if time.time() - ts < CACHE_TTL:
                return value
            del self._cache[key]
        return None

    def _cache_set(self, key: str, value: Any) -> None:
        self._cache[key] = (time.time(), value)

    def _get(self, path: str, params: dict | None = None) -> Any:
        """Make a GET request and return parsed JSON.

        The IGVF Snovault API returns HTTP 404 when a valid search produces
        zero results (the body is still valid JSON with total=0).  We treat
        404 as a normal empty-result response for search endpoints.
        """
        cache_key = f"{path}|{params}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        resp = self._client.get(path, params=params)
        if resp.status_code == 404 and path.startswith("/search"):
            # Snovault returns 404 for empty search results — parse as normal
            data = resp.json()
            self._cache_set(cache_key, data)
            return data
        resp.raise_for_status()
        data = resp.json()
        self._cache_set(cache_key, data)
        return data

    def get_object(self, path: str) -> dict:
        """
        Fetch a single object by its @id path or accession.

        Args:
            path: Object path (e.g. '/measurement-sets/IGVFDS1234ABCD/')
                  or just an accession like 'IGVFDS1234ABCD'.

        Returns:
            Full JSON object dict.
        """
        if not path.startswith("/"):
            # Bare accession — resolve via search
            return self._get(f"/{path}/", params={"format": "json"})
        return self._get(path, params={"format": "json"})

    def search(
        self,
        object_type: str,
        *,
        limit: int = 25,
        offset: int = 0,
        field_filters: dict[str, str] | None = None,
    ) -> dict:
        """
        Search the IGVF portal.

        Args:
            object_type: IGVF object type (e.g. 'MeasurementSet', 'AnalysisSet',
                         'TabularFile', 'Gene').
            limit: Max results to return.
            offset: Pagination offset (use 'from' query param).
            field_filters: Additional query filters (e.g. {'status': 'released',
                           'samples.taxa': 'Homo sapiens'}).

        Returns:
            Raw search response dict. Results are in '@graph' key.
        """
        params: dict[str, Any] = {
            "type": object_type,
            "limit": max(0, limit),
            "format": "json",
        }
        if offset:
            params["from"] = offset
        if field_filters:
            params.update(field_filters)

        return self._get("/search/", params=params)

    def search_all(
        self,
        object_type: str,
        *,
        field_filters: dict[str, str] | None = None,
        max_results: int = 500,
    ) -> list[dict]:
        """
        Search and return all @graph results, paginating automatically.

        Args:
            object_type: IGVF object type.
            field_filters: Additional filters.
            max_results: Safety cap on total results.

        Returns:
            List of result dicts from @graph.
        """
        all_results: list[dict] = []
        batch_size = min(100, max_results)
        offset = 0

        while len(all_results) < max_results:
            resp = self.search(
                object_type,
                limit=batch_size,
                offset=offset,
                field_filters=field_filters,
            )
            graph = resp.get("@graph", [])
            if not graph:
                break
            all_results.extend(graph)
            total = resp.get("total", 0)
            if len(all_results) >= total:
                break
            offset += batch_size

        return all_results[:max_results]

    def get_file_download_url(self, file_path: str) -> str:
        """
        Get the download URL for a file object.

        Args:
            file_path: File @id path or accession (e.g. 'IGVFFI1234ABCD').

        Returns:
            Full download URL string.
        """
        obj = self.get_object(file_path)
        href = obj.get("href", "")
        if href and not href.startswith("http"):
            href = f"{self.base_url}{href}"
        return href

    def clear_cache(self) -> dict:
        """Clear the cache and return stats."""
        size = len(self._cache)
        self._cache.clear()
        return {"cleared": size}

    def close(self) -> None:
        self._client.close()
