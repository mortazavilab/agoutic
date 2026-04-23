"""Progress visualization helpers for AGOUTIC UI."""

from typing import List, Dict
import streamlit as st


def stepper(steps: List[str], current: int, completed: List[int] | None = None, failed: List[int] | None = None) -> None:
    completed = completed or []
    failed = failed or []
    cols = st.columns(len(steps))
    for i, step in enumerate(steps):
        if i in failed:
            icon, color = "❌", "#b86d5d"
        elif i in completed:
            icon, color = "✅", "#6fa07a"
        elif i == current:
            icon, color = "🔄", "#d2a16f"
        else:
            icon, color = "○", "#b9b2a6"
        with cols[i]:
            st.markdown(
                f"<div style='text-align:center'>"
                f"<div style='font-size:18px;color:{color}'>{icon}</div>"
                f"<div style='font-size:12px'>{step}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )


def segmented_progress(parts: List[Dict]) -> None:
    seg_html = ""
    for p in parts:
        name = p.get("name", "")
        pct = max(0, min(100, int(p.get("percentage", 0))))
        status = (p.get("status") or "pending").lower()
        if status == "complete":
            color = "#6fa07a"
        elif status in ("active", "running"):
            color = "#d2a16f"
        elif status == "failed":
            color = "#b86d5d"
        else:
            color = "#5a5e61"
        seg_html += f"<div style='flex:{max(1,pct)};background:{color};height:22px' title='{name}'></div>"
    st.markdown(f"<div style='display:flex;border-radius:10px;overflow:hidden;border:1px solid #3a3f42'>{seg_html}</div>", unsafe_allow_html=True)


def timeline(stages: List[Dict]) -> None:
    for s in stages:
        status = (s.get("status") or "pending").lower()
        icon = {"complete": "✅", "running": "🔄", "failed": "❌", "pending": "○"}.get(status, "○")
        st.markdown(f"- {icon} **{s.get('name','Stage')}** {('- ' + s.get('details')) if s.get('details') else ''}")


def progress_stats(stats: Dict[str, str | int | float]) -> None:
    if not stats:
        return
    cols = st.columns(len(stats))
    for i, (k, v) in enumerate(stats.items()):
        with cols[i]:
            st.metric(k, v)
