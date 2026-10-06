"""Search, compare, save, and export PubMed/PMC literature."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import make_authenticated_request, require_auth
from components.cards import section_header
from literature_helpers import citation_export

API_URL = os.getenv("AGOUTIC_API_URL", "http://127.0.0.1:8000")
SEARCH_URL = f"{API_URL}/literature"

st.set_page_config(page_title="Literature Search", page_icon="📚", layout="wide")
require_auth(API_URL)
section_header(
    "Literature Search",
    "Search PubMed and PMC, inspect evidence, compare papers, and export citations",
    icon="📚",
)
st.caption(
    "Searches default to relevance ranking. Optional date, organism, article type, "
    "study-design, and open-full-text filters are applied at PubMed retrieval."
)


def _request(method: str, url: str, **kwargs) -> dict | list:
    response = make_authenticated_request(method, url, timeout=45, **kwargs)
    if not response.ok:
        try:
            detail = response.json().get("detail", response.text)
        except (ValueError, AttributeError):
            detail = response.text
        raise RuntimeError(f"Literature service returned HTTP {response.status_code}: {detail}")
    return response.json()


def _table(papers: list[dict], key: str) -> list[dict]:
    rows = []
    for paper in papers:
        evidence = paper.get("evidence") or {}
        summary = paper.get("summary") or {}
        finding = evidence.get("findings") or evidence.get("conclusion") or summary.get("text", "")
        rows.append({
            "Select": False,
            "Paper": str(paper.get("title") or "Untitled article"),
            "Year": str(paper.get("year") or paper.get("publication_date") or ""),
            "Organism/model": ", ".join(paper.get("organisms") or []) or "Not specified",
            "Study type": ", ".join(paper.get("study_type") or []) or "Not specified",
            "Key finding": str(finding or "")[:220],
            "Evidence": {
                "full_text": "PMC full text",
                "abstract": "PubMed abstract",
                "metadata": "Metadata only",
            }.get(str(paper.get("evidence_source") or "metadata"), "Unknown"),
            "PMID": str(paper.get("pmid") or ""),
            "PubMed": str(paper.get("pubmed_url") or ""),
            "_key": key,
        })
    return rows


def _render_papers(papers: list[dict], table_key: str) -> list[dict]:
    if not papers:
        st.info("No papers to display.")
        return []
    text_filter = st.text_input(
        "Filter displayed papers",
        key=f"{table_key}_text_filter",
        placeholder="Filter title, abstract, authors, or topic…",
    ).strip().lower()
    organism_values = sorted({
        organism
        for paper in papers
        for organism in paper.get("organisms") or []
    })
    organism_filter = st.selectbox(
        "Organism/model",
        ["Any"] + organism_values,
        key=f"{table_key}_organism_filter",
        label_visibility="collapsed",
    )
    visible = [
        paper for paper in papers
        if (not text_filter or text_filter in " ".join([
            str(paper.get("title") or ""),
            str(paper.get("abstract") or ""),
            str(paper.get("authors") or ""),
            str(paper.get("rank_rationale") or ""),
        ]).lower())
        and (organism_filter == "Any" or organism_filter in (paper.get("organisms") or []))
    ]
    st.caption(f"Showing {len(visible)} of {len(papers)} papers. Select rows to compare or export.")
    rows = _table(visible, table_key)
    frame = pd.DataFrame(rows).drop(columns=["_key"])
    edited = st.data_editor(
        frame,
        key=f"{table_key}_results_table",
        hide_index=True,
        use_container_width=True,
        disabled=[column for column in frame.columns if column != "Select"],
        column_config={
            "Select": st.column_config.CheckboxColumn("Select", default=False),
            "PubMed": st.column_config.LinkColumn("PubMed", display_text="Open"),
            "Key finding": st.column_config.TextColumn("Key finding", width="large"),
        },
    )
    selected_pmids = {
        str(row.get("PMID") or "")
        for row in edited.to_dict("records")
        if row.get("Select")
    }
    selected = [paper for paper in visible if str(paper.get("pmid") or "") in selected_pmids]
    return selected


pending_query = st.session_state.pop("literature_pending_query", None)
if pending_query is not None:
    st.session_state["literature_query_input"] = pending_query

search_tab, saved_tab = st.tabs(["Search papers", "Saved searches and alerts"])

with search_tab:
    previous = st.session_state.get("literature_search_result") or {}
    previous_filters = previous.get("filters") or {}
    with st.form("literature_search_form"):
        query = st.text_input(
            "Research topic",
            value=st.session_state.get("literature_query_input", previous.get("query", "")),
            key="literature_query_input",
            placeholder="e.g. similarities and differences between human and mouse kidneys",
        )
        first, second, third = st.columns(3)
        with first:
            result_count = st.selectbox("Number of papers", [5, 10, 15, 20], index=1)
            sort_by = st.selectbox("Sort order", ["relevance", "recency"])
        with second:
            organism = st.selectbox(
                "Organism",
                ["Any", "human", "mouse", "human and mouse", "rat", "zebrafish"],
                help="Use “human and mouse” to require evidence in both species; leave Any for broad retrieval.",
            )
            article_type = st.selectbox(
                "Article type",
                [
                    "Any",
                    "journal article",
                    "comparative study",
                    "clinical trial",
                    "randomized controlled trial",
                    "review",
                    "systematic review",
                    "meta-analysis",
                    "case report",
                ],
            )
        with third:
            study_design = st.selectbox(
                "Study design",
                [
                    "Any",
                    "functional study",
                    "functional genomics",
                    "comparative study",
                    "comparative genomics",
                    "systems genetics",
                    "population genetics",
                    "population genomics",
                    "genome-wide association study",
                    "quantitative trait loci study",
                    "single-cell study",
                    "animal study",
                    "cohort study",
                    "case-control study",
                    "cross-sectional study",
                ],
            )
            open_access_only = st.checkbox("PMC open full text only")
        date_col1, date_col2 = st.columns(2)
        with date_col1:
            use_year_from = st.checkbox("Set earliest publication year", key="lit_use_year_from")
            year_from = st.number_input(
                "From year", min_value=1800, max_value=2100,
                value=int(previous_filters.get("year_from") or 2000),
                disabled=not use_year_from,
            )
        with date_col2:
            use_year_to = st.checkbox("Set latest publication year", key="lit_use_year_to")
            year_to = st.number_input(
                "To year", min_value=1800, max_value=2100,
                value=int(previous_filters.get("year_to") or 2100),
                disabled=not use_year_to,
            )
        submitted = st.form_submit_button("Search PubMed and PMC", type="primary")

    if submitted:
        if not query.strip():
            st.error("Enter a research topic to search.")
        elif use_year_from and use_year_to and year_from > year_to:
            st.error("The earliest year must not be later than the latest year.")
        else:
            payload = {
                "query": query.strip(),
                "result_count": result_count,
                "year_from": int(year_from) if use_year_from else None,
                "year_to": int(year_to) if use_year_to else None,
                "organism": None if organism == "Any" else organism,
                "article_type": None if article_type == "Any" else article_type,
                "study_design": None if study_design == "Any" else study_design,
                "open_access_only": open_access_only,
                "sort_by": sort_by,
            }
            try:
                with st.spinner("Searching PubMed and PMC…"):
                    st.session_state["literature_search_result"] = _request(
                        "POST", f"{SEARCH_URL}/search", json=payload
                    )
            except Exception as exc:
                st.error(f"Could not search the literature: {exc}")

    result = st.session_state.get("literature_search_result") or {}
    papers = result.get("papers") or []
    if result:
        st.subheader(f"{len(papers)} paper(s) for “{result.get('query', '')}”")
        if result.get("search_query"):
            with st.expander("Search details"):
                st.code(result["search_query"])
                st.json(result.get("filters") or {})
        selected = _render_papers(papers, "literature_search")
        if selected:
            st.subheader("Compare selected papers")
            comparison = []
            for paper in selected:
                evidence = paper.get("evidence") or {}
                summary = paper.get("summary") or {}
                comparison.append({
                    "Paper": paper.get("title", ""),
                    "Year": paper.get("year") or paper.get("publication_date", ""),
                    "Organisms": ", ".join(paper.get("organisms") or []) or "Not specified",
                    "Study type": ", ".join(paper.get("study_type") or []) or "Not specified",
                    "Methods": evidence.get("methods") or "Not explicitly labeled in available evidence",
                    "Findings": evidence.get("findings") or summary.get("text") or "Not available",
                    "Limitations": evidence.get("limitations") or "Not explicitly labeled in available evidence",
                    "Evidence source": paper.get("evidence_source", "metadata"),
                })
            st.dataframe(pd.DataFrame(comparison), hide_index=True, use_container_width=True)
        export_papers = selected or papers
        st.caption("Exports include selected papers; if none are selected, all search results are exported.")
        export_columns = st.columns(3)
        for column, kind in zip(export_columns, ("CSV", "RIS", "BibTeX")):
            content, mime, filename = citation_export(export_papers, kind)
            column.download_button(
                f"Download {kind}",
                data=content,
                file_name=filename,
                mime=mime,
                key=f"literature_export_{kind}",
                disabled=not export_papers,
            )
        st.subheader("Paper evidence and links")
        for paper in papers:
            title = str(paper.get("title") or "Untitled article")
            pmid = str(paper.get("pmid") or "")
            with st.expander(f"{title} (PMID: {pmid})"):
                st.markdown(
                    f"[PubMed record]({paper.get('pubmed_url', '')})"
                    + (f" · [PMC full text]({paper['pmc_url']})" if paper.get("pmc_url") else "")
                    + (f" · [DOI](https://doi.org/{paper['doi']})" if paper.get("doi") else "")
                )
                summary = paper.get("summary") or {}
                st.markdown(f"**Evidence source:** {paper.get('evidence_source', 'metadata')}")
                st.write(summary.get("text", "No abstract evidence available."))
                st.caption(f"Why it matched: {paper.get('rank_rationale', 'Not reported')}")
                evidence = paper.get("evidence") or {}
                for field, label in (
                    ("methods", "Methods"),
                    ("findings", "Findings"),
                    ("conclusion", "Conclusion"),
                    ("limitations", "Limitations"),
                ):
                    if evidence.get(field):
                        st.markdown(f"**{label}:** {evidence[field]}")
                sections = paper.get("abstract_sections") or []
                if sections:
                    st.markdown("**Structured abstract**")
                    for section in sections:
                        label = section.get("label") or "Abstract"
                        st.markdown(f"**{label}:** {section.get('text', '')}")

        st.divider()
        st.subheader("Save this search")
        with st.form("save_literature_search_form"):
            saved_name = st.text_input("Name for this search", value=result.get("query", "")[:120])
            alert_enabled = st.checkbox(
                "Enable on-demand new-paper checks",
                value=True,
                help="Saved searches are checked when you explicitly select “Check for new papers.”",
            )
            save_submitted = st.form_submit_button("Save search")
        if save_submitted:
            if not saved_name.strip():
                st.error("Enter a name for this saved search.")
            else:
                save_payload = {
                    **(result.get("filters") or {}),
                    "query": result.get("query", ""),
                    "name": saved_name.strip(),
                    "known_pmids": [str(paper.get("pmid")) for paper in papers if paper.get("pmid")],
                    "alert_enabled": alert_enabled,
                }
                try:
                    _request("POST", f"{SEARCH_URL}/saved-searches", json=save_payload)
                    st.success("Saved search and current PMID snapshot.")
                except Exception as exc:
                    st.error(f"Could not save this search: {exc}")

with saved_tab:
    st.caption(
        "Saved searches are private to your account. New-paper checks run only when requested; "
        "no email or background notifications are sent."
    )
    if st.button("Refresh saved searches", key="refresh_saved_literature"):
        st.session_state.pop("literature_saved_searches", None)
    if "literature_saved_searches" not in st.session_state:
        try:
            st.session_state["literature_saved_searches"] = _request(
                "GET", f"{SEARCH_URL}/saved-searches"
            )
        except Exception as exc:
            st.error(f"Could not load saved searches: {exc}")
            st.session_state["literature_saved_searches"] = []
    for saved in st.session_state.get("literature_saved_searches", []):
        with st.container(border=True):
            st.markdown(f"**{saved['name']}**")
            st.caption(saved["query"])
            st.caption(
                f"New-paper checks: {'enabled' if saved.get('alert_enabled') else 'disabled'}"
                + (f" · Last checked {saved['last_checked_at']}" if saved.get("last_checked_at") else "")
            )
            check_col, run_col, delete_col = st.columns(3)
            with check_col:
                if saved.get("alert_enabled") and st.button(
                    "Check for new papers", key=f"check_saved_{saved['id']}"
                ):
                    try:
                        alert = _request(
                            "POST", f"{SEARCH_URL}/saved-searches/{saved['id']}/check"
                        )
                        st.session_state[f"literature_alert_{saved['id']}"] = alert
                        st.session_state.pop("literature_saved_searches", None)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not check this search: {exc}")
            with run_col:
                if st.button("Run search", key=f"run_saved_{saved['id']}"):
                    try:
                        result = _request(
                            "POST",
                            f"{SEARCH_URL}/search",
                            json={"query": saved["query"], **saved.get("filters", {})},
                        )
                        st.session_state["literature_search_result"] = result
                        st.session_state["literature_pending_query"] = saved["query"]
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not run saved search: {exc}")
            with delete_col:
                if st.button("Delete", key=f"delete_saved_{saved['id']}"):
                    try:
                        _request("DELETE", f"{SEARCH_URL}/saved-searches/{saved['id']}")
                        st.session_state.pop("literature_saved_searches", None)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not delete this search: {exc}")
            alert = st.session_state.get(f"literature_alert_{saved['id']}")
            if alert:
                if alert.get("new_count"):
                    st.success(f"Found {alert['new_count']} new paper(s).")
                    _render_papers(alert.get("new_papers") or [], f"alert_{saved['id']}")
                else:
                    st.info("No new papers were found since the previous snapshot.")
