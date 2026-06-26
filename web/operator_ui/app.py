"""Streamlit operator UI — entry point."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import streamlit as st

from web.operator_ui.job_manager import JobManager
from web.operator_ui.theme import (
    inject_theme,
    load_preferences,
    render_settings_dialog,
    render_skip_link,
    render_topbar,
)

_preferences = load_preferences()
st.set_page_config(
    page_title="Qlib 量化交易系统",
    layout="wide",
    initial_sidebar_state="collapsed" if _preferences.sidebar_collapsed else "expanded",
)

inject_theme(_preferences)
render_skip_link()

# ---------------------------------------------------------------------------
# Sticky top bar — page title slot + settings gear.
# The gear opens the settings modal (theme / color convention / sidebar
# default), replacing the legacy sidebar Appearance expander.
# ---------------------------------------------------------------------------
if render_topbar(title="Qlib 量化交易系统", subtitle="运维控制台"):
    render_settings_dialog(_preferences)

# ---------------------------------------------------------------------------
# Sidebar — brand header, global status indicator, nav icon injection
# All rendered in the sidebar *before* pg.run() so they survive st.stop()
# calls on empty-state pages.
# ---------------------------------------------------------------------------

# ``list_jobs`` returns RAW job.json dicts — no success->completed
# normalization (that happens in job_io for the Jobs page), so the real
# terminal vocabulary here is success / failed / stop_failed / stopped /
# partial / pending / queued. Bucket against that: count ``stop_failed`` as a
# failure and ``partial`` as a completion, and drop the dead ``completed`` /
# ``ok`` aliases the runner never writes. (``stopped`` / ``pending`` /
# ``queued`` fall through to idle — not an alert or a completion.)
_jobs = JobManager.list_jobs()
_running = sum(1 for j in _jobs if j.get("status") == "running")
_failed = sum(1 for j in _jobs if j.get("status") in ("failed", "stop_failed"))
_completed = sum(1 for j in _jobs if j.get("status") in ("success", "partial"))

_status_class = "idle"
_status_text = "全部空闲"
if _running:
    _status_class = "running"
    _status_text = f"{_running} 个作业运行中"
elif _failed:
    # Surface both counts so the indicator stays informative after the
    # operator runs a successful job alongside a previously-failed one;
    # the prior text just said "1 个作业失败" forever, which felt stuck.
    _status_class = "error"
    _status_text = f"{_failed} 个失败 · {_completed} 个完成"
elif _completed:
    _status_text = f"全部空闲 · {_completed} 个完成"

_ICON_MAP = {
    "作业": "\U0001f4cb",            # 📋
    "配置运行": "\U0001f680",        # 🚀
    "结果": "\U0001f4c8",            # 📈
    "滚动验证": "\U0001f501",        # 🔁
    "设计系统": "\U0001f3a8",        # 🎨
}
# Nav-icon injection. A ``MutationObserver`` replaces the old
# ``setTimeout`` retry loop (UI review P2-3): the loop ran at most 10
# times over ~1.2s then gave up, so on a slow first paint — or any
# later Streamlit rerun that rebuilds the sidebar DOM — the icons
# silently dropped. The observer re-applies ``decorate()`` whenever the
# document subtree changes (rAF-debounced so a burst of mutations
# coalesces into one pass), and installs itself exactly once via a flag
# on ``window.parent`` so repeated script emissions don't stack
# observers.
_ICON_SCRIPT = """
<script>
(function() {
  var icons = __ICON_MAP__;
  var doc = window.parent.document;
  function decorate() {
    var links = doc.querySelectorAll(
      '[data-testid="stSidebarNav"] a, [data-testid="stSidebarNav"] span'
    );
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
  if (!window.parent.__qv2NavIconObserver) {
    var scheduled = false;
    var observer = new MutationObserver(function() {
      if (scheduled) return;
      scheduled = true;
      window.parent.requestAnimationFrame(function() {
        scheduled = false;
        decorate();
      });
    });
    observer.observe(doc.body, {childList: true, subtree: true});
    window.parent.__qv2NavIconObserver = observer;
  }
})();
</script>
""".replace("__ICON_MAP__", str(_ICON_MAP).replace("'", '"'))

with st.sidebar:
    st.html(
        """
<div class="qv2-sidebar-brand">
  <span class="qv2-sidebar-logo">📈</span>
  <span class="qv2-sidebar-brand-text">
    Qlib 量化交易<br>
    <span class="qv2-brand-version">系统 V2</span>
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

_navigation: dict[str, list[Any]] = {
    "运行": [
        st.Page(str(_PAGES_DIR / "jobs.py"), title="作业"),
        st.Page(str(_PAGES_DIR / "config_run.py"), title="配置运行"),
    ],
    "分析": [
        st.Page(str(_PAGES_DIR / "results.py"), title="结果"),
        st.Page(str(_PAGES_DIR / "walk_forward.py"), title="滚动验证"),
        # P3-6b: read-only inspector of the PRODUCTION bundle (integrity stamp
        # + health + on-demand 06 validation). The UI never builds bundles.
        st.Page(str(_PAGES_DIR / "data_inspect.py"), title="数据检视"),
    ],
}

# Design-system demo page is for visual QA only — it doesn't read any
# runtime artifacts and adding it to the operator nav let new operators
# misread "设计系统" as a real settings page (UI review P1-12). Gate it
# behind an opt-in env var so design QA can still preview tokens via
# ``QV2_SHOW_DESIGN_SYSTEM=1 python scripts/run_ui.py`` without
# polluting the production menu.
if os.environ.get("QV2_SHOW_DESIGN_SYSTEM", "").strip():
    _navigation["系统"] = [
        st.Page(str(_PAGES_DIR / "design_system.py"), title="设计系统"),
    ]

pg = st.navigation(_navigation)

pg.run()
