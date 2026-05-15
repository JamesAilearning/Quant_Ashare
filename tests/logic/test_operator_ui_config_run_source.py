"""Source-level regression guards for Streamlit Config & Run wiring."""

from __future__ import annotations

import unittest
from pathlib import Path


class ConfigRunPageSourceTests(unittest.TestCase):
    def test_provider_uri_input_is_outside_run_form(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        provider_input_pos = source.index('"provider_uri *"')
        run_form_pos = source.index('with st.form("run_form")')

        self.assertLess(
            provider_input_pos,
            run_form_pos,
            "provider_uri must stay outside st.form so Run disabled state rerenders.",
        )

    def test_early_stopping_ui_rejects_zero(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn(
            'st.number_input("early_stopping_rounds", value=50, min_value=1)',
            source,
        )


if __name__ == "__main__":
    unittest.main()
