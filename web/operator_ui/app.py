"""Streamlit operator UI — entry point."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from web.operator_ui.theme import inject_theme, load_preferences, render_appearance_controls

st.set_page_config(page_title="Qlib Trading System", layout="wide")

_preferences = render_appearance_controls(load_preferences())
inject_theme(_preferences)

st.title("Qlib Trading System V2")

_PAGES_DIR = Path(__file__).resolve().parent / "pages"

pg = st.navigation({
    "Run": [
        st.Page(str(_PAGES_DIR / "config_run.py"), title="Config & Run"),
    ],
    "Analyze": [
        st.Page(str(_PAGES_DIR / "results.py"), title="Results"),
        st.Page(str(_PAGES_DIR / "walk_forward.py"), title="Walk-Forward"),
    ],
    "History": [
        st.Page(str(_PAGES_DIR / "run_history.py"), title="Run History"),
    ],
    "System": [
        st.Page(str(_PAGES_DIR / "design_system.py"), title="Design System"),
    ],
})

pg.run()
