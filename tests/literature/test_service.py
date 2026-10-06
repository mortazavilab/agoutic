import httpx
import pytest
import xml.etree.ElementTree as ET

from literature.service import LiteratureService, _build_pubmed_query


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


def test_natural_language_query_is_normalized_and_expanded():
    query, profile = _build_pubmed_query(
        "find me papers on the similarities and difference between human and mouse kidneys"
    )

    assert "find" not in query
    assert "papers" not in query
    assert "human[Title/Abstract]" in query
    assert "mice[MeSH Terms]" in query
    assert "renal[Title/Abstract]" in query
    assert '"similarities"[Title/Abstract]' in query
    assert profile.terms == ("human", "mouse", "kidney")
    assert profile.comparative is True


@pytest.mark.asyncio
async def test_specific_comparison_search_keeps_relevant_candidate_outside_first_ten():
    ids = [str(value) for value in range(1, 21)]
    ids[12] = "39121855"
    observed_search: dict[str, str] = {}
    article_xml = []
    for pmid in ids:
        if pmid == "39121855":
            title = "Comparative single-cell analyses identify shared and divergent features of human and mouse kidney development"
            abstract = "We compare human and mouse kidney development and identify shared and divergent features."
            year = "2024"
        else:
            title = f"Kidney study {pmid} in human and mouse models"
            abstract = "This study investigates kidney disease in human and mouse models."
            year = "2025"
        article_xml.append(
            f"<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID><Article>"
            f"<ArticleTitle>{title}</ArticleTitle><Abstract><AbstractText>{abstract}</AbstractText></Abstract>"
            f"<Journal><Title>Kidney Research</Title><JournalIssue><PubDate><Year>{year}</Year>"
            "</PubDate></JournalIssue></Journal></Article></MedlineCitation>"
            "<PubmedData><ArticleIdList /></PubmedData></PubmedArticle>"
        )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("esearch.fcgi"):
            observed_search["term"] = request.url.params["term"]
            observed_search["retmax"] = request.url.params["retmax"]
            return httpx.Response(200, json={"esearchresult": {"idlist": ids}})
        if request.url.path.endswith("efetch.fcgi"):
            return httpx.Response(
                200,
                text="<PubmedArticleSet>" + "".join(article_xml) + "</PubmedArticleSet>",
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await LiteratureService(client=client).search_literature(
            "find me papers on the similarities and difference between human and mouse kidneys"
        )

    assert observed_search["retmax"] == "100"
    assert "find" not in observed_search["term"]
    assert len(result["papers"]) == 10
    assert result["papers"][0]["pmid"] == "39121855"
    assert "Comparative single-cell analyses" in result["papers"][0]["title"]


def test_pubmed_identifiers_do_not_leak_from_cited_references():
    article = ET.fromstring("""
      <PubmedArticle>
        <MedlineCitation><PMID>33679620</PMID><Article>
          <ArticleTitle>Ferroptosis in diabetic renal injury</ArticleTitle>
          <ELocationID EIdType="doi">10.3389/fendo.2021.626390</ELocationID>
          <Journal><Title>Frontiers in Endocrinology</Title></Journal>
        </Article></MedlineCitation>
        <PubmedData>
          <ArticleIdList>
            <ArticleId IdType="pubmed">33679620</ArticleId>
            <ArticleId IdType="pmc">PMC8739816</ArticleId>
          </ArticleIdList>
          <ReferenceList><Reference><ArticleIdList>
            <ArticleId IdType="doi">10.1089/ars.2016.6664</ArticleId>
            <ArticleId IdType="pmc">PMC5069735</ArticleId>
          </ArticleIdList></Reference></ReferenceList>
        </PubmedData>
      </PubmedArticle>
    """)

    record = LiteratureService()._parse_pubmed_article(article)

    assert record["pmid"] == "33679620"
    assert record["doi"] == "10.3389/fendo.2021.626390"
    assert record["pmcid"] == "PMC8739816"


def test_pubmed_query_includes_selected_search_filters():
    query, profile = _build_pubmed_query(
        "kidney development",
        organism="mouse",
        article_type="review",
        study_design="single-cell study",
        open_access_only=True,
    )

    assert "mice[MeSH Terms]" in query
    assert "Review[Publication Type]" in query
    assert '"single-cell"[Title/Abstract]' in query
    assert "free full text[sb]" in query
    assert profile.terms == ("kidney", "development")


def test_study_filters_cover_nonclinical_and_mixed_species_research():
    query, _ = _build_pubmed_query(
        "kidney development",
        organism="human and mouse",
        article_type="journal article",
        study_design="systems genetics",
    )

    assert "humans[MeSH Terms]" in query
    assert "mice[MeSH Terms]" in query
    assert " AND (" in query
    assert "Journal Article[Publication Type]" in query
    assert '"systems genetics"[Title/Abstract]' in query


@pytest.mark.asyncio
async def test_search_applies_years_and_returns_structured_evidence():
    observed_params = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("esearch.fcgi"):
            observed_params.update(dict(request.url.params))
            return httpx.Response(200, json={"esearchresult": {"idlist": ["41"]}})
        if request.url.path.endswith("efetch.fcgi"):
            return httpx.Response(200, text="""
              <PubmedArticleSet><PubmedArticle><MedlineCitation>
                <PMID>41</PMID><Article>
                  <ArticleTitle>Mouse kidney development study</ArticleTitle>
                  <Abstract><AbstractText Label="METHODS">Single-cell sequencing was performed.</AbstractText>
                    <AbstractText Label="RESULTS">A shared kidney cell population was identified.</AbstractText>
                    <AbstractText Label="CONCLUSIONS">The findings support a developmental role.</AbstractText>
                  </Abstract>
                  <Journal><Title>Development</Title><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal>
                  <PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>
                </Article><MeshHeadingList><MeshHeading><DescriptorName>Mice</DescriptorName></MeshHeading></MeshHeadingList>
              </MedlineCitation><PubmedData><ArticleIdList /></PubmedData></PubmedArticle></PubmedArticleSet>
            """)
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await LiteratureService(client=client).search_literature(
            "mouse kidney",
            year_from=2020,
            year_to=2025,
            organism="mouse",
        )

    paper = result["papers"][0]
    assert observed_params["mindate"] == "2020"
    assert observed_params["maxdate"] == "2025"
    assert observed_params["datetype"] == "pdat"
    assert paper["abstract_sections"][0]["label"] == "METHODS"
    assert paper["evidence"]["methods"] == "Single-cell sequencing was performed."
    assert paper["evidence"]["findings"] == "A shared kidney cell population was identified."
    assert paper["organisms"] == ["Mouse"]
    assert paper["study_type"] == ["Journal Article"]
    assert paper["rank_rationale"] == "title matches mouse, kidney"


@pytest.mark.asyncio
async def test_open_access_filter_returns_only_records_with_pmc_full_text():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"idlist": ["1", "2"]}})
        if request.url.path.endswith("efetch.fcgi") and request.url.params.get("db") == "pubmed":
            return httpx.Response(200, text="""
              <PubmedArticleSet>
                <PubmedArticle><MedlineCitation><PMID>1</PMID><Article>
                  <ArticleTitle>Kidney paper without accessible full text</ArticleTitle>
                  <Abstract><AbstractText>Kidney evidence.</AbstractText></Abstract>
                </Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="pmc">PMC1</ArticleId></ArticleIdList></PubmedData></PubmedArticle>
                <PubmedArticle><MedlineCitation><PMID>2</PMID><Article>
                  <ArticleTitle>Kidney paper with accessible full text</ArticleTitle>
                  <Abstract><AbstractText>Kidney full text evidence.</AbstractText></Abstract>
                </Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="pmc">PMC2</ArticleId></ArticleIdList></PubmedData></PubmedArticle>
              </PubmedArticleSet>
            """)
        if request.url.path.endswith("oa.fcgi"):
            if request.url.params["id"] == "PMC1":
                return httpx.Response(200, text="<OA><error>Not Open Access</error></OA>")
            return httpx.Response(200, text="<OA><record id='PMC2' /></OA>")
        if request.url.path.endswith("efetch.fcgi") and request.url.params.get("db") == "pmc":
            return httpx.Response(200, text="<article><body><sec><title>Methods</title><p>Method details.</p></sec><sec><title>Results</title><p>Result details.</p></sec></body></article>")
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await LiteratureService(client=client).search_literature(
            "kidney", result_count=1, open_access_only=True
        )

    assert result["total"] == 1
    assert result["papers"][0]["pmid"] == "2"
    assert result["papers"][0]["availability"] == "full_text"
    assert result["papers"][0]["evidence"]["methods"] == "Method details."
