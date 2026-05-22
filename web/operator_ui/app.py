"""Streamlit operator UI — entry point."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from web.operator_ui.job_manager import JobManager
from web.operator_ui.theme import inject_theme, load_preferences, render_appearance_controls

st.set_page_config(page_title="Qlib Trading System", layout="wide")

_preferences = render_appearance_controls(load_preferences())
inject_theme(_preferences)

# ---------------------------------------------------------------------------
# Sidebar — brand header, global status indicator, nav icon injection
# All rendered in the sidebar *before* pg.run() so they survive st.stop()
# calls on empty-state pages.
# ---------------------------------------------------------------------------

_jobs = JobManager.list_jobs()
_running = sum(1 for j in _jobs if j.get("status") == "running")
_failed = sum(1 for j in _jobs if j.get("status") == "failed" and _running == 0)

_status_class = "idle"
_status_text = "All idle"
if _running:
    _status_class = "running"
    _status_text = "1 job running" if _running == 1 else f"{_running} jobs running"
elif _failed:
    _status_class = "error"
    _status_text = "1 job failed" if _failed == 1 else f"{_failed} jobs failed"

_ICON_MAP = {
    "Config & Run": "\U0001f680",   # 🚀
    "Results": "\U0001f4c8",        # 📈
    "Walk-Forward": "\U0001f501",   # 🔁
    "Run History": "\U0001f4da",    # 📚
    "Design System": "\U0001f3a8",  # 🎨
}
_ICON_SCRIPT = """
<script>
(function() {
  var icons = %s;
  var attempt = 0;
  function decorate() {
    var links = window.parent.document.querySelectorAll(
      '[data-testid="stSidebarNav"] a, [data-testid="stSidebarNav"] span'
    );
    if (links.length === 0 && attempt < 10) {
      attempt++;
      setTimeout(decorate, 120);
      return;
    }
    links.forEach(function(el) {
      var text = (el.textContent || '').trim();
      if (icons[text] && !el.querySelector('.qv2-nav-icon')) {
        var icon = document.createElement('span');
        icon.className = 'qv2-nav-icon';
        icon.textContent = icons[text];
        el.insertBefore(icon, el.firstChild);
      }
    });
  }
  decorate();
})();
</script>
""" % str(_ICON_MAP).replace("'", '"')

with st.sidebar:
    st.html(
        """
<div class="qv2-sidebar-brand">
  <span class="qv2-sidebar-logo">📈</span>
  <span class="qv2-sidebar-brand-text">
    Qlib Trading<br>
    <span class="qv2-brand-version">System V2</span>
  </span>
</div>
""",
        width="content",
        unsafe_allow_javascript=False,
    )

    st.html(
        f"""
<div class="qv2-sidebar-footer">
  <div class="qv2-sidebar-status qv2-sidebar-status--{_status_class}">
    <span class="qv2-status-dot"></span>
    <span class="qv2-status-label">{_status_text}</span>
  </div>
</div>
""",
        width="content",
        unsafe_allow_javascript=False,
    )

    st.html(_ICON_SCRIPT, width="content", unsafe_allow_javascript=True)

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
_PAGES_DIR = Path(__file__).resolve().parent / "pages"

pg = st.navigation(
    {
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
    },
)

pg.run()
