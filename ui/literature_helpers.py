"""Pure helpers shared by the literature page and its tests."""
from __future__ import annotations

import csv
import io
import re


def _citation_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _csv_safe(value: object) -> str:
    text = _citation_text(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def citation_export(papers: list[dict], kind: str) -> tuple[str, str, str]:
    if kind == "CSV":
        output = io.StringIO()
        fields = [
            "pmid", "title", "authors", "journal", "publication_date", "doi",
            "organisms", "study_type", "evidence_source", "pubmed_url", "pmc_url",
        ]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for paper in papers:
            writer.writerow({
                "pmid": _csv_safe(paper.get("pmid", "")),
                "title": _csv_safe(paper.get("title", "")),
                "authors": _csv_safe("; ".join(paper.get("authors") or [])),
                "journal": _csv_safe(paper.get("journal", "")),
                "publication_date": _csv_safe(paper.get("publication_date", "")),
                "doi": _csv_safe(paper.get("doi", "")),
                "organisms": _csv_safe("; ".join(paper.get("organisms") or [])),
                "study_type": _csv_safe("; ".join(paper.get("study_type") or [])),
                "evidence_source": _csv_safe(paper.get("evidence_source", "")),
                "pubmed_url": _csv_safe(paper.get("pubmed_url", "")),
                "pmc_url": _csv_safe(paper.get("pmc_url", "")),
            })
        return output.getvalue(), "text/csv", "literature-results.csv"
    if kind == "RIS":
        entries = []
        for paper in papers:
            lines = ["TY  - JOUR", f"TI  - {_citation_text(paper.get('title', ''))}"]
            lines.extend(f"AU  - {_citation_text(author)}" for author in paper.get("authors") or [])
            if paper.get("journal"):
                lines.append(f"JO  - {_citation_text(paper['journal'])}")
            year = _citation_text(paper.get("year") or paper.get("publication_date") or "")[:4]
            if year:
                lines.append(f"PY  - {year}")
            if paper.get("doi"):
                lines.append(f"DO  - {_citation_text(paper['doi'])}")
            if paper.get("pmid"):
                lines.append(f"AN  - PMID:{_citation_text(paper['pmid'])}")
            if paper.get("pubmed_url"):
                lines.append(f"UR  - {_citation_text(paper['pubmed_url'])}")
            lines.append("ER  - ")
            entries.append("\n".join(lines))
        return (
            "\n\n".join(entries) + ("\n" if entries else ""),
            "application/x-research-info-systems",
            "literature-results.ris",
        )
    if kind == "BibTeX":
        entries = []
        for paper in papers:
            authors = paper.get("authors") or []
            author_key = re.sub(r"\W+", "", _citation_text(authors[0]).split()[-1]) if authors else "paper"
            year = _citation_text(paper.get("year") or paper.get("publication_date") or "")[:4]
            key = author_key + year
            fields = {
                "title": paper.get("title", ""),
                "author": " and ".join(authors),
                "journal": paper.get("journal", ""),
                "year": year,
                "doi": paper.get("doi", ""),
                "pmid": paper.get("pmid", ""),
                "url": paper.get("pubmed_url", ""),
            }
            rendered = ",\n".join(
                f"  {field} = {{{_citation_text(value).replace('{', '').replace('}', '')}}}"
                for field, value in fields.items() if value
            )
            entries.append(f"@article{{{key},\n{rendered}\n}}")
        return (
            "\n\n".join(entries) + ("\n" if entries else ""),
            "application/x-bibtex",
            "literature-results.bib",
        )
    raise ValueError(f"Unsupported citation export format: {kind}")
