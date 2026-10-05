from types import SimpleNamespace

import pytest

from cortex.chat_stages.second_pass import SecondPassStage


@pytest.mark.asyncio
async def test_literature_results_bypass_generic_llm_table_summary(monkeypatch):
    async def no_remote_context(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "cortex.chat_stages.second_pass._build_remote_stage_approval_context",
        no_remote_context,
    )
    ctx = SimpleNamespace(
        all_results={
            "literature": [{
                "tool": "search_literature",
                "params": {"query": "P53", "user_id": "private-user-id"},
                "data": {
                    "query": "P53",
                    "total": 1,
                    "papers": [{
                        "title": "TP53 gene function",
                        "authors": ["A Author"],
                        "journal": "Genomics",
                        "publication_date": "2025",
                        "pmid": "12345",
                        "pmcid": "PMC123",
                        "doi": None,
                        "pubmed_url": "https://pubmed.ncbi.nlm.nih.gov/12345/",
                        "pmc_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/",
                        "availability": "full_text",
                        "evidence_source": "full_text",
                        "summary": {
                            "text": "This paper reports findings about TP53.",
                            "evidence_source": "full_text",
                        },
                    }],
                },
            }],
        },
        skip_second_pass=False,
        provenance=[{
            "source": "literature",
            "tool": "search_literature",
            "params": {"query": "P53", "user_id": "private-user-id"},
            "timestamp": "2026-10-05T00:00:00Z",
            "success": True,
        }],
        active_skill="literature_search",
        session=None,
        project_id="project-id",
        user=SimpleNamespace(id="private-user-id"),
        message="find papers on P53",
        remote_stage_approval_context=None,
        clean_markdown="",
        request_id="request-id",
    )

    await SecondPassStage().run(ctx)

    assert "[TP53 gene function](https://pubmed.ncbi.nlm.nih.gov/12345/)" in ctx.clean_markdown
    assert "PMC open full text" in ctx.clean_markdown
    assert "This paper reports findings about TP53." in ctx.clean_markdown
    assert "interactive table" not in ctx.clean_markdown.lower()
    assert "private-user-id" not in ctx.clean_markdown
