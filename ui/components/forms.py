"""Form composition helpers for AGOUTIC UI."""

from typing import Dict, Any
import streamlit as st


def grouped_section(title: str, helper: str = "") -> None:
    st.markdown(f"#### {title}")
    if helper:
        st.caption(helper)


def review_panel(params: Dict[str, Any], title: str = "Review") -> None:
    with st.container(border=True):
        st.markdown(f"**{title}**")
        for k, v in params.items():
            if v in (None, "", [], {}):
                continue
            c1, c2 = st.columns([1, 2])
            with c1:
                st.caption(k)
            with c2:
                st.write(v)


def advanced_panel(label: str, expanded: bool = False):
    return st.expander(label, expanded=expanded)


def validation_messages(errors: list[str] | None = None, warnings: list[str] | None = None, successes: list[str] | None = None) -> None:
    for e in errors or []:
        st.error(e)
    for w in warnings or []:
        st.warning(w)
    for s in successes or []:
        st.success(s)
