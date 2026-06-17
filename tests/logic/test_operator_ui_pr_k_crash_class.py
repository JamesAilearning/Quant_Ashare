"""PR-K (阶段4): the Streamlit UI crash class — source-level regression pins.

Streamlit 1.57 raises StreamlitAPIException when ``st.session_state[k]`` is
written for a widget key ``k`` AFTER that widget is instantiated in the same
run. The fix for every site is to move the mutation into an ``on_click`` /
``on_change`` CALLBACK (runs before the widgets are re-instantiated). CI does not
install the ``[ui]`` extra (no streamlit), so these are source-level pins — they
read the page text and assert the safe callback wiring is present and the inline
crash form is gone.
"""

from __future__ import annotations

import unittest
from pathlib import Path

_JOBS = Path("web/operator_ui/pages/jobs.py")
_CONFIG_RUN = Path("web/operator_ui/pages/config_run.py")
_RESULTS_RENDER = Path("web/operator_ui/pages/_results_render.py")


class JobsCrashClassTests(unittest.TestCase):
    def setUp(self) -> None:
        self.src = _JOBS.read_text(encoding="utf-8")

    def test_quick_date_presets_use_on_click_callback(self) -> None:
        self.assertIn("on_click=_apply_quick_range", self.src)
        # the old inline form `if st.button("今天", ...):` is gone
        self.assertNotIn('if st.button("今天"', self.src)

    def test_presets_and_cleanup_use_cn_today_not_host_date(self) -> None:
        # The quick-date presets + cleanup eligibility must use CN today (UTC+8),
        # consistent with the CN date filter/display, so they agree even on a
        # non-CN (e.g. UTC container) server (Codex P2).
        self.assertIn("cn_today()", self.src)
        self.assertNotIn("date.today()", self.src)

    def test_filter_chip_clears_use_on_click_callbacks(self) -> None:
        self.assertIn("on_click=_clear_chip", self.src)
        self.assertIn("on_click=_clear_all_filters", self.src)

    def test_bulk_cleanup_uses_on_click_callback(self) -> None:
        self.assertIn("on_click=_run_bulk_cleanup", self.src)
        # the confirm-checkbox reset now lives in the callback, not inline after
        # the delete loop
        body = self.src[self.src.index("def _run_bulk_cleanup"):]
        body = body[: body.index("\n\n\n")]
        self.assertIn('st.session_state["jobs_cleanup_confirm"] = False', body)


class ConfigRunCrashClassTests(unittest.TestCase):
    def test_auto_fix_button_uses_on_click_callback(self) -> None:
        src = _CONFIG_RUN.read_text(encoding="utf-8")
        self.assertIn("on_click=fix_callable", src)
        # the old inline `fix_callable()` + `st.rerun()` invocation is gone
        self.assertNotIn("fix_callable()\n", src)


class ResultsZipCacheTests(unittest.TestCase):
    def test_bundle_zip_is_cached(self) -> None:
        src = _RESULTS_RENDER.read_text(encoding="utf-8")
        self.assertIn("@st.cache_data", src)
        self.assertIn("_cached_bundle_zip", src)
        # the uncached direct call on every rerun is gone
        self.assertNotIn("bundle_zip_bytes(run_dir)", src)


if __name__ == "__main__":
    unittest.main()
