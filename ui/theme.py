"""Shared UI theme tokens and styling helpers for AGOUTIC Streamlit UI."""

import streamlit as st

# Palette: soft-dark warm minimalist
COLOR_BG_PRIMARY = "#181a1b"
COLOR_BG_SECONDARY = "#232628"
COLOR_BG_TERTIARY = "#2d3133"

COLOR_TEXT_PRIMARY = "#ece8df"
COLOR_TEXT_SECONDARY = "#b9b2a6"
COLOR_TEXT_MUTED = "#8f887c"

COLOR_ACCENT_AMBER = "#d2a16f"
COLOR_ACCENT_SAGE = "#7fa08f"
COLOR_ACCENT_BLUE = "#6f8ea7"

COLOR_SUCCESS = "#6fa07a"
COLOR_WARNING = "#d2a16f"
COLOR_ERROR = "#b86d5d"
COLOR_INFO = "#6f8ea7"

COLOR_BORDER = "#3a3f42"
COLOR_BORDER_SOFT = "#2e3336"

RADIUS_SM = "10px"
RADIUS_MD = "14px"
RADIUS_LG = "16px"

SPACE_SM = "8px"
SPACE_MD = "12px"
SPACE_LG = "16px"
SPACE_XL = "24px"


def status_color(status: str) -> str:
    mapping = {
        "info": COLOR_INFO,
        "warning": COLOR_WARNING,
        "error": COLOR_ERROR,
        "success": COLOR_SUCCESS,
        "pending": COLOR_TEXT_SECONDARY,
        "queued": COLOR_TEXT_SECONDARY,
        "running": COLOR_WARNING,
        "active": COLOR_WARNING,
        "blocked": COLOR_INFO,
        "failed": COLOR_ERROR,
        "cancelled": COLOR_TEXT_MUTED,
        "complete": COLOR_SUCCESS,
        "completed": COLOR_SUCCESS,
        "approved": COLOR_SUCCESS,
        "rejected": COLOR_ERROR,
        "new": COLOR_TEXT_SECONDARY,
        "done": COLOR_SUCCESS,
    }
    return mapping.get((status or "").lower(), COLOR_TEXT_SECONDARY)


def get_plotly_template() -> dict:
    return {
        "layout": {
            "paper_bgcolor": COLOR_BG_PRIMARY,
            "plot_bgcolor": COLOR_BG_SECONDARY,
            "font": {"color": COLOR_TEXT_PRIMARY, "size": 12},
            "xaxis": {"gridcolor": COLOR_BORDER_SOFT, "tickfont": {"color": COLOR_TEXT_SECONDARY}},
            "yaxis": {"gridcolor": COLOR_BORDER_SOFT, "tickfont": {"color": COLOR_TEXT_SECONDARY}},
            "legend": {
                "bgcolor": COLOR_BG_SECONDARY,
                "bordercolor": COLOR_BORDER,
                "borderwidth": 1,
                "font": {"color": COLOR_TEXT_PRIMARY},
            },
            "margin": {"l": 8, "r": 8, "t": 40, "b": 8},
        }
    }


def inject_global_css() -> None:
    st.markdown(
        f"""
        <style>
        [data-testid="stAppViewContainer"] {{
            background: radial-gradient(1200px 600px at 10% -10%, #202426 0%, {COLOR_BG_PRIMARY} 55%);
        }}
        [data-testid="stSidebar"] {{
            background: linear-gradient(180deg, #1e2123 0%, #191b1c 100%);
            border-right: 1px solid {COLOR_BORDER_SOFT};
        }}
        [data-testid="stVerticalBlockBorderWrapper"] {{
            border-radius: {RADIUS_MD};
        }}
        [data-testid="stExpander"] {{
            border: 1px solid {COLOR_BORDER};
            border-radius: {RADIUS_MD};
            overflow: hidden;
            background: {COLOR_BG_SECONDARY};
        }}
        [data-testid="stMetric"] {{
            background: {COLOR_BG_SECONDARY};
            border: 1px solid {COLOR_BORDER_SOFT};
            border-radius: {RADIUS_MD};
            padding: 0.35rem 0.55rem;
        }}
        div[data-baseweb="select"] > div,
        div[data-baseweb="input"] > div,
        textarea {{
            background: {COLOR_BG_SECONDARY} !important;
            border-color: {COLOR_BORDER} !important;
            border-radius: {RADIUS_MD} !important;
        }}
        button[kind] {{
            border-radius: {RADIUS_MD} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
