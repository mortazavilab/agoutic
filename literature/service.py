"""PubMed/PMC retrieval, normalization, ranking, and evidence attribution."""
from __future__ import annotations

import asyncio
import html
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import httpx

from literature.config import (
    DEFAULT_RESULT_COUNT,
    MAX_QUERY_LENGTH,
    MAX_RESULT_COUNT,
    NCBI_API_KEY_REQUESTS_PER_SECOND,
    NCBI_EUTILS_URL,
    NCBI_PMC_OA_URL,
    NCBI_PUBLIC_REQUESTS_PER_SECOND,
    NCBI_TIMEOUT_SECONDS,
)

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{1,}")
_QUALITY_TERMS = frozenset({"randomized", "systematic review", "meta-analysis", "cohort"})
_INTENT_WORDS = frozenset({
    "find", "get", "give", "look", "me", "paper", "papers", "publication",
    "publications", "study", "studies", "article", "articles", "literature",
    "pubmed", "about", "on", "for", "the", "a", "an", "and", "or", "of",
    "in", "to", "with", "between", "regarding",
})
_COMPARISON_CUES = frozenset({
    "compare", "compared", "comparison", "comparisons", "comparative",
    "similar", "similarity", "similarities", "difference", "differences",
    "differ", "different", "divergent", "shared",
})
_COMPARISON_SEARCH_TERMS = (
    "comparative", "comparison", "comparisons", "similar", "similarity",
    "similarities", "difference", "differences", "differ", "different",
    "divergent", "shared",
)
_PUBMED_GROUPS: dict[str, tuple[str, ...]] = {
    "human": (
        "humans[MeSH Terms]", "human[Title/Abstract]", "humans[Title/Abstract]",
        '"Homo sapiens"[Title/Abstract]',
    ),
    "mouse": (
        "mice[MeSH Terms]", "mouse[Title/Abstract]", "mice[Title/Abstract]",
        "murine[Title/Abstract]",
    ),
    "kidney": (
        "kidney[MeSH Terms]", "kidney[Title/Abstract]", "kidneys[Title/Abstract]",
        "renal[Title/Abstract]",
    ),
    "p53": (
        "TP53[MeSH Terms]", "TP53[Title/Abstract]", "p53[Title/Abstract]",
    ),
}


class LiteratureServiceError(RuntimeError):
    """An upstream failure that should be reported explicitly to the caller."""


@dataclass(frozen=True)
class NCBICredentials:
    email: str | None = None
    api_key: str | None = None


@dataclass(frozen=True)
class QueryProfile:
    terms: tuple[str, ...]
    comparative: bool


def _query_profile(query: str) -> QueryProfile:
    """Remove conversational/search boilerplate while retaining topic concepts."""
    tokens = [token.lower() for token in _WORD_RE.findall(query)]
    comparative = any(token in _COMPARISON_CUES for token in tokens)
    terms: list[str] = []
    for token in tokens:
        if token in _INTENT_WORDS or token in _COMPARISON_CUES:
            continue
        if token in {"kidneys"}:
            token = "kidney"
        elif token in {"mice", "murine"}:
            token = "mouse"
        elif token in {"humans"}:
            token = "human"
        elif token == "tp53":
            token = "p53"
        if token not in terms:
            terms.append(token)
    return QueryProfile(terms=tuple(terms), comparative=comparative)


def _build_pubmed_query(query: str) -> tuple[str, QueryProfile]:
    """Build a fielded PubMed query that avoids treating prompt wording as science terms."""
    profile = _query_profile(query)
    if not profile.terms:
        raise ValueError("Include at least one scientific topic term in the literature query.")

    groups: list[str] = []
    for term in profile.terms:
        alternatives = _PUBMED_GROUPS.get(term, (f'"{term}"[Title/Abstract]',))
        groups.append(f"({' OR '.join(alternatives)})")
    if profile.comparative:
        comparisons = " OR ".join(
            f'"{term}"[Title/Abstract]' for term in _COMPARISON_SEARCH_TERMS
        )
        groups.append(f"({comparisons})")
    return " AND ".join(groups), profile


class RateLimiter:
    def __init__(self) -> None:
        self._next_request_at = 0.0
        self._lock = asyncio.Lock()

    async def wait(self, requests_per_second: float) -> None:
        interval = 1 / max(requests_per_second, 0.1)
        async with self._lock:
            now = time.monotonic()
            delay = self._next_request_at - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_request_at = time.monotonic() + interval


class LiteratureService:
    """Client for NCBI E-utilities with bounded, source-aware responses."""

    def __init__(self, credentials_loader=None, client: httpx.AsyncClient | None = None) -> None:
        self._credentials_loader = credentials_loader
        self._client = client
        self._limiter = RateLimiter()

    async def search_literature(
        self, query: str, result_count: int = DEFAULT_RESULT_COUNT, user_id: str | None = None
    ) -> dict[str, Any]:
        cleaned_query = " ".join(str(query or "").split())
        if not cleaned_query:
            raise ValueError("A literature search query is required.")
        if len(cleaned_query) > MAX_QUERY_LENGTH:
            raise ValueError(f"Literature queries must be at most {MAX_QUERY_LENGTH} characters.")
        if not 1 <= int(result_count) <= MAX_RESULT_COUNT:
            raise ValueError(f"result_count must be between 1 and {MAX_RESULT_COUNT}.")
        pubmed_query, query_profile = _build_pubmed_query(cleaned_query)

        credentials = await self._load_credentials(user_id)
        async with self._get_client() as client:
            # Fetch a broader candidate set, then rank against the user's topic
            # before spending additional requests on PMC full-text enrichment.
            candidate_count = min(max(int(result_count) * 10, 100), 200)
            ids = await self._search_ids(client, pubmed_query, candidate_count, credentials)
            if not ids:
                return {
                    "query": cleaned_query,
                    "search_query": pubmed_query,
                    "total": 0,
                    "papers": [],
                    "notice": "No PubMed records matched this query.",
                }
            records = await self._fetch_records(client, ids, credentials)
            records.sort(
                key=lambda record: self._score(record, query_profile),
                reverse=True,
            )
            normalized = [
                await self._normalize_record(client, record, query_profile, credentials)
                for record in records[:int(result_count)]
            ]
        for rank, paper in enumerate(normalized, start=1):
            paper["rank"] = rank
            paper.pop("relevance_score", None)
        return {
            "query": cleaned_query,
            "search_query": pubmed_query,
            "total": len(normalized),
            "papers": normalized,
        }

    async def _load_credentials(self, user_id: str | None) -> NCBICredentials:
        if not user_id or self._credentials_loader is None:
            return NCBICredentials()
        loaded = self._credentials_loader(user_id)
        if hasattr(loaded, "__await__"):
            loaded = await loaded
        return loaded if isinstance(loaded, NCBICredentials) else NCBICredentials()

    def _get_client(self):
        if self._client is not None:
            class _BorrowedClient:
                async def __aenter__(_self):
                    return self._client

                async def __aexit__(_self, *_args):
                    return False
            return _BorrowedClient()
        return httpx.AsyncClient(timeout=httpx.Timeout(NCBI_TIMEOUT_SECONDS), follow_redirects=True)

    async def _request(
        self, client: httpx.AsyncClient, url: str, params: dict[str, str], credentials: NCBICredentials
    ) -> httpx.Response:
        if credentials.email:
            params["email"] = credentials.email
        if credentials.api_key:
            params["api_key"] = credentials.api_key
        await self._limiter.wait(
            NCBI_API_KEY_REQUESTS_PER_SECOND if credentials.api_key else NCBI_PUBLIC_REQUESTS_PER_SECOND
        )
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response
        except httpx.TimeoutException as exc:
            raise LiteratureServiceError("PubMed did not respond before the request timeout.") from exc
        except httpx.HTTPStatusError as exc:
            raise LiteratureServiceError(f"PubMed request failed with HTTP {exc.response.status_code}.") from exc
        except httpx.HTTPError as exc:
            raise LiteratureServiceError("Unable to contact PubMed.") from exc

    async def _search_ids(
        self, client: httpx.AsyncClient, query: str, count: int, credentials: NCBICredentials
    ) -> list[str]:
        response = await self._request(
            client, f"{NCBI_EUTILS_URL}/esearch.fcgi",
            {"db": "pubmed", "term": query, "retmode": "json", "retmax": str(count), "sort": "relevance"},
            credentials,
        )
        try:
            return [str(value) for value in response.json()["esearchresult"]["idlist"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise LiteratureServiceError("PubMed returned an invalid search response.") from exc

    async def _fetch_records(
        self, client: httpx.AsyncClient, ids: list[str], credentials: NCBICredentials
    ) -> list[dict[str, Any]]:
        response = await self._request(
            client, f"{NCBI_EUTILS_URL}/efetch.fcgi",
            {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"},
            credentials,
        )
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            raise LiteratureServiceError("PubMed returned invalid article metadata.") from exc
        return [self._parse_pubmed_article(article) for article in root.findall(".//PubmedArticle")]

    def _parse_pubmed_article(self, article: ET.Element) -> dict[str, Any]:
        medline = article.find("MedlineCitation")
        if medline is None:
            raise LiteratureServiceError("PubMed returned an article without citation metadata.")
        article_node = medline.find("Article")
        pmid = (medline.findtext("PMID") or "").strip()
        if article_node is None or not pmid:
            raise LiteratureServiceError("PubMed returned an incomplete citation.")
        abstract = " ".join("".join(node.itertext()).strip() for node in article_node.findall(".//AbstractText"))
        pmcid = ""
        doi = ""
        for elocation in article_node.findall("./ELocationID"):
            if elocation.attrib.get("EIdType", "").lower() == "doi":
                doi = (elocation.text or "").strip()
                break
        for article_id in article.findall("./PubmedData/ArticleIdList/ArticleId"):
            if article_id.attrib.get("IdType") == "pmc":
                pmcid = pmcid or (article_id.text or "").strip()
            if article_id.attrib.get("IdType") == "doi" and not doi:
                doi = (article_id.text or "").strip()
        authors = []
        for author in article_node.findall("./AuthorList/Author"):
            collective = (author.findtext("CollectiveName") or "").strip()
            name = collective or " ".join(
                value for value in ((author.findtext("ForeName") or "").strip(), (author.findtext("LastName") or "").strip())
                if value
            )
            if name:
                authors.append(name)
        date = (
            article_node.findtext("./Journal/JournalIssue/PubDate/Year")
            or medline.findtext("./DateCompleted/Year")
            or ""
        )
        return {
            "pmid": pmid,
            "pmcid": pmcid,
            "doi": doi,
            "title": " ".join("".join(article_node.find("ArticleTitle").itertext()).split()) if article_node.find("ArticleTitle") is not None else "",
            "abstract": abstract,
            "authors": authors,
            "journal": (article_node.findtext("./Journal/Title") or "").strip(),
            "publication_date": date,
            "publication_types": [value.text or "" for value in article_node.findall("./PublicationTypeList/PublicationType")],
        }

    async def _normalize_record(
        self,
        client: httpx.AsyncClient,
        record: dict[str, Any],
        query_profile: QueryProfile,
        credentials: NCBICredentials,
    ) -> dict[str, Any]:
        full_text = ""
        if record["pmcid"]:
            full_text = await self._fetch_pmc_text(client, record["pmcid"], credentials)
        evidence_text = full_text or record["abstract"]
        provenance = "full_text" if full_text else ("abstract" if evidence_text else "metadata")
        summary = self._summary(evidence_text, provenance)
        return {
            "rank": 0,
            "title": record["title"],
            "authors": record["authors"],
            "journal": record["journal"],
            "publication_date": record["publication_date"],
            "pmid": record["pmid"],
            "pmcid": record["pmcid"] or None,
            "doi": record["doi"] or None,
            "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{record['pmid']}/",
            "pmc_url": f"https://pmc.ncbi.nlm.nih.gov/articles/{record['pmcid']}/" if record["pmcid"] else None,
            "availability": "full_text" if full_text else ("abstract_only" if record["abstract"] else "metadata_only"),
            "summary": summary,
            "evidence_source": provenance,
            "rank_rationale": "Relevance to the query, with recency and study-design cues as secondary factors.",
            "relevance_score": self._score(record, query_profile),
        }

    async def _fetch_pmc_text(
        self, client: httpx.AsyncClient, pmcid: str, credentials: NCBICredentials
    ) -> str:
        # PMC's OA service confirms that full text is openly retrievable.
        try:
            oa_response = await self._request(client, NCBI_PMC_OA_URL, {"id": pmcid}, credentials)
            if "error" in oa_response.text.lower() or "record" not in oa_response.text.lower():
                return ""
            response = await self._request(
                client, f"{NCBI_EUTILS_URL}/efetch.fcgi",
                {"db": "pmc", "id": pmcid.removeprefix("PMC"), "retmode": "xml"},
                credentials,
            )
            root = ET.fromstring(response.text)
            paragraphs = [" ".join("".join(node.itertext()).split()) for node in root.findall(".//body//p")]
            return " ".join(paragraphs[:8]).strip()
        except (LiteratureServiceError, ET.ParseError):
            return ""

    def _score(self, record: dict[str, Any], query_profile: QueryProfile) -> float:
        title = str(record.get("title") or "").lower()
        abstract = str(record.get("abstract") or "").lower()
        searchable = " ".join((title, abstract, " ".join(record.get("publication_types") or [])))
        relevance = 0
        for term in query_profile.terms:
            aliases = {
                "human": ("human", "humans", "homo sapiens"),
                "mouse": ("mouse", "mice", "murine"),
                "kidney": ("kidney", "kidneys", "renal"),
                "p53": ("p53", "tp53"),
            }.get(term, (term,))
            if any(alias in title for alias in aliases):
                relevance += 120
            elif any(alias in abstract for alias in aliases):
                relevance += 50
        if query_profile.comparative and any(cue in searchable for cue in _COMPARISON_CUES):
            relevance += 80
        quality = 5 * sum(term in searchable for term in _QUALITY_TERMS)
        try:
            recency = max(0, min(20, int(record["publication_date"][:4]) - 2000))
        except (TypeError, ValueError):
            recency = 0
        return relevance + quality + recency

    @staticmethod
    def _summary(text: str, provenance: str) -> dict[str, str]:
        cleaned = html.unescape(" ".join(text.split()))
        if not cleaned:
            return {
                "text": "No abstract or open full text was available; this entry is metadata only.",
                "evidence_source": "metadata",
            }
        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        return {"text": " ".join(sentences[:2])[:800], "evidence_source": provenance}
