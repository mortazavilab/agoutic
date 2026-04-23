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

COLOR_PLOT_PAPER = "#ffffff"
COLOR_PLOT_SURFACE = "#FAFAF7"
COLOR_PLOT_TEXT = "#1A1A1A"
COLOR_PLOT_TEXT_MUTED = "#555555"
COLOR_PLOT_GRID = "#E8E8E8"
COLOR_PLOT_BORDER = "#2B2B2B"
COLOR_PLOT_SERIES = [
    "#2E5A87",
    "#E69F00",
    "#56B4E9",
    "#009E73",
    "#F0E442",
    "#0072B2",
    "#D55E00",
    "#CC79A7",
    "#000000",
]

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
            "paper_bgcolor": COLOR_PLOT_PAPER,
            "plot_bgcolor": COLOR_PLOT_SURFACE,
            "colorway": COLOR_PLOT_SERIES,
            "font": {
                "color": COLOR_PLOT_TEXT,
                "size": 11,
                "family": "Inter, IBM Plex Sans, Helvetica Neue, Source Sans Pro, Arial, sans-serif",
            },
            "title": {
                "font": {"color": COLOR_PLOT_TEXT, "size": 16},
                "x": 0.02,
                "xanchor": "left",
            },
            "xaxis": {
                "showgrid": False,
                "gridcolor": COLOR_PLOT_GRID,
                "linecolor": COLOR_PLOT_BORDER,
                "tickcolor": COLOR_PLOT_BORDER,
                "linewidth": 1.1,
                "showline": True,
                "ticks": "outside",
                "tickfont": {"color": COLOR_PLOT_TEXT_MUTED, "size": 10},
                "title": {"font": {"color": COLOR_PLOT_BORDER, "size": 12}},
                "title_standoff": 16,
                "automargin": True,
                "zeroline": False,
            },
            "yaxis": {
                "showgrid": True,
                "gridcolor": COLOR_PLOT_GRID,
                "linecolor": COLOR_PLOT_BORDER,
                "tickcolor": COLOR_PLOT_BORDER,
                "linewidth": 1.1,
                "showline": True,
                "ticks": "outside",
                "tickfont": {"color": COLOR_PLOT_TEXT_MUTED, "size": 10},
                "title": {"font": {"color": COLOR_PLOT_BORDER, "size": 12}},
                "title_standoff": 16,
                "automargin": True,
                "zeroline": False,
            },
            "legend": {
                "bgcolor": COLOR_PLOT_PAPER,
                "borderwidth": 0,
                "font": {"color": COLOR_PLOT_TEXT_MUTED, "size": 10},
                "title": {"font": {"color": COLOR_PLOT_BORDER, "size": 11}},
            },
            "hoverlabel": {
                "bgcolor": COLOR_PLOT_PAPER,
                "bordercolor": "#D0D0D0",
                "font": {
                    "color": COLOR_PLOT_TEXT,
                    "size": 11,
                    "family": "Inter, IBM Plex Sans, Helvetica Neue, Source Sans Pro, Arial, sans-serif",
                },
            },
            "margin": {"l": 96, "r": 42, "t": 86, "b": 84},
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
        [data-testid="stPlotlyChart"],
        [data-testid="stImage"] {{
            background: rgba(255, 255, 255, 0.96);
            border: 1px solid rgba(215, 222, 232, 0.85);
            border-radius: {RADIUS_MD};
            box-shadow: 0 10px 24px rgba(15, 23, 42, 0.12);
            padding: 0.65rem;
        }}
        [data-testid="stPlotlyChart"] {{
            overflow: visible;
        }}
        [data-testid="stImage"] {{
            overflow: hidden;
        }}
        [data-testid="stPlotlyChart"] > div,
        [data-testid="stImage"] > div {{
            background: transparent;
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
