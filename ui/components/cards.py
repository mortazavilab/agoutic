"""Reusable presentational card helpers for AGOUTIC UI."""

from typing import Any, Dict
import streamlit as st
from theme import status_color, COLOR_TEXT_PRIMARY, COLOR_TEXT_SECONDARY


def section_header(title: str, helper: str = "", icon: str = "") -> None:
    heading = f"{icon} {title}" if icon else title
    st.markdown(f"### {heading}")
    if helper:
        st.caption(helper)


def status_chip(status: str, label: str | None = None, icon: str = "") -> None:
    text = label or status.title()
    icon_prefix = f"{icon} " if icon else ""
    color = status_color(status)
    st.markdown(
        f"<span style='display:inline-block;padding:4px 10px;border-radius:999px;"
        f"border:1px solid {color}66;background:{color}22;color:{color};font-size:12px;font-weight:600;'>"
        f"{icon_prefix}{text}</span>",
        unsafe_allow_html=True,
    )


def metadata_row(items: Dict[str, Any]) -> None:
    if not items:
        return
    cols = st.columns(len(items))
    for i, (k, v) in enumerate(items.items()):
        with cols[i]:
            st.markdown(
                f"<div style='font-size:11px;color:{COLOR_TEXT_SECONDARY};text-transform:uppercase;'>{k}</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div style='font-size:14px;color:{COLOR_TEXT_PRIMARY};font-weight:600'>{v}</div>",
                unsafe_allow_html=True,
            )


def stat_tile(label: str, value: Any, icon: str = "") -> None:
    shown = f"{icon} {value}" if icon else value
    st.metric(label, shown)


def empty_state(title: str, message: str, icon: str = "📭") -> None:
    st.markdown(
        f"<div style='text-align:center;padding:22px;border-radius:14px;border:1px dashed #3a3f42;'>"
        f"<div style='font-size:32px'>{icon}</div>"
        f"<div style='font-size:16px;font-weight:700;margin-top:6px'>{title}</div>"
        f"<div style='font-size:13px;color:#b9b2a6;margin-top:4px'>{message}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def info_callout(message: str, kind: str = "info", icon: str = "") -> None:
    color = status_color(kind)
    prefix = f"{icon} " if icon else ""
    st.markdown(
        f"<div style='border-left:3px solid {color};background:{color}18;padding:10px 12px;border-radius:10px;'>"
        f"<span style='color:{color};font-weight:600'>{prefix}{message}</span></div>",
        unsafe_allow_html=True,
    )
