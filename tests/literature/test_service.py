import httpx
import pytest

from literature.service import LiteratureService


def _response(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("esearch.fcgi"):
        return httpx.Response(200, json={"esearchresult": {"idlist": ["1", "2"]}})
    if request.url.path.endswith("efetch.fcgi") and request.url.params.get("db") == "pubmed":
        return httpx.Response(200, text="""
        <PubmedArticleSet>
          <PubmedArticle><MedlineCitation><PMID>1</PMID><Article>
            <ArticleTitle>Gene function study</ArticleTitle><Abstract><AbstractText>Gene function is tested.</AbstractText></Abstract>
            <Journal><Title>Journal</Title><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal>
            <PublicationTypeList><PublicationType>Randomized Controlled Trial</PublicationType></PublicationTypeList>
          </Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/a</ArticleId></ArticleIdList></PubmedData></PubmedArticle>
          <PubmedArticle><MedlineCitation><PMID>2</PMID><Article>
            <ArticleTitle>Unrelated paper</ArticleTitle><Abstract><AbstractText>Other results.</AbstractText></Abstract>
            <Journal><Title>Journal</Title><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue></Journal>
          </Article></MedlineCitation><PubmedData><ArticleIdList /></PubmedData></PubmedArticle>
        </PubmedArticleSet>
        """)
    raise AssertionError(f"Unexpected request: {request.url}")


@pytest.mark.asyncio
async def test_search_returns_ranked_abstract_attributed_papers():
    transport = httpx.MockTransport(_response)
    async with httpx.AsyncClient(transport=transport) as client:
        service = LiteratureService(client=client)
        result = await service.search_literature("gene function", result_count=10)

    assert result["total"] == 2
    assert result["papers"][0]["pmid"] == "1"
    assert result["papers"][0]["availability"] == "abstract_only"
    assert result["papers"][0]["summary"]["evidence_source"] == "abstract"
    assert result["papers"][0]["pubmed_url"].endswith("/1/")


@pytest.mark.asyncio
async def test_search_rejects_empty_queries():
    service = LiteratureService()
    with pytest.raises(ValueError, match="required"):
        await service.search_literature(" ")
