import csv
import io

import pytest

from ui.literature_helpers import citation_export


PAPER = {
    "pmid": "123",
    "title": "Mouse kidney development",
    "authors": ["Ada Author", "Bea Author"],
    "journal": "Kidney Journal",
    "publication_date": "2024",
    "year": "2024",
    "doi": "10.1000/example",
    "organisms": ["Mouse"],
    "study_type": ["Journal Article"],
    "evidence_source": "abstract",
    "pubmed_url": "https://pubmed.ncbi.nlm.nih.gov/123/",
    "pmc_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/",
}


@pytest.mark.parametrize("kind", ["CSV", "RIS", "BibTeX"])
def test_citation_export_includes_paper_identifiers_and_citation(kind):
    content, mime, filename = citation_export([PAPER], kind)

    assert content
    assert "123" in content
    assert "Mouse kidney development" in content
    assert filename.endswith({"CSV": ".csv", "RIS": ".ris", "BibTeX": ".bib"}[kind])
    assert mime


def test_csv_export_writes_structured_columns():
    content, _, _ = citation_export([PAPER], "CSV")
    row = next(csv.DictReader(io.StringIO(content)))

    assert row["authors"] == "Ada Author; Bea Author"
    assert row["organisms"] == "Mouse"
    assert row["pmc_url"].endswith("PMC123/")


def test_citation_export_rejects_unknown_format():
    with pytest.raises(ValueError, match="Unsupported"):
        citation_export([PAPER], "XML")


def test_csv_export_neutralizes_spreadsheet_formulas():
    content, _, _ = citation_export([{**PAPER, "title": "=HYPERLINK(\"x\")"}], "CSV")
    row = next(csv.DictReader(io.StringIO(content)))

    assert row["title"].startswith("'=")
