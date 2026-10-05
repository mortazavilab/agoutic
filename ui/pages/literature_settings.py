"""Manage optional per-user NCBI credentials for literature searches."""
from __future__ import annotations

import os

import streamlit as st

from auth import make_authenticated_request, require_auth
from components.cards import section_header

API_URL = os.getenv("AGOUTIC_API_URL", "http://127.0.0.1:8000")
ENDPOINT = f"{API_URL}/user/ncbi-credentials"

st.set_page_config(page_title="Literature Settings", page_icon="📚", layout="wide")
require_auth(API_URL)
section_header("Literature Settings", "Optional NCBI access for PubMed and PMC searches", icon="📚")
st.caption(
    "Without saved credentials, searches use NCBI public request limits. "
    "Saved API keys are encrypted and are never displayed after submission."
)


def _load_status() -> dict:
    try:
        response = make_authenticated_request("GET", ENDPOINT, timeout=10)
        if response.status_code == 200:
            return response.json()
        st.error(f"Could not load NCBI settings: HTTP {response.status_code}.")
    except Exception as exc:
        st.error(f"Could not load NCBI settings: {exc}")
    return {"email": "", "has_api_key": False}


status = _load_status()
st.info(
    "An NCBI API key is saved." if status.get("has_api_key") else
    "No NCBI API key is saved; public request limits are active."
)

with st.form("ncbi_credentials"):
    email = st.text_input(
        "NCBI contact email",
        value=status.get("email") or "",
        help="Used only in requests to NCBI, as requested by its API policy.",
    )
    api_key = st.text_input(
        "Replace NCBI API key",
        type="password",
        help="Leave blank to keep the existing key. This field is never prefilled.",
    )
    submitted = st.form_submit_button("Save settings")
    if submitted:
        payload = {"email": email}
        if api_key:
            payload["api_key"] = api_key
        try:
            response = make_authenticated_request("PUT", ENDPOINT, json=payload, timeout=10)
            if response.status_code == 200:
                st.success("NCBI settings saved.")
            else:
                st.error(response.json().get("detail", f"HTTP {response.status_code}"))
        except Exception as exc:
            st.error(f"Could not save NCBI settings: {exc}")

if status.get("has_api_key") and st.button("Remove saved API key", type="secondary"):
    try:
        response = make_authenticated_request("DELETE", f"{ENDPOINT}/api-key", timeout=10)
        if response.status_code == 200:
            st.success("Saved NCBI API key removed.")
            st.rerun()
        else:
            st.error(response.json().get("detail", f"HTTP {response.status_code}"))
    except Exception as exc:
        st.error(f"Could not remove NCBI API key: {exc}")
