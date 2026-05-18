"""Source-level regression guards for Streamlit Config & Run wiring."""

from __future__ import annotations

import unittest
from pathlib import Path


class ConfigRunPageSourceTests(unittest.TestCase):
    def test_training_controls_are_not_inside_streamlit_form(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertNotIn(
            'with st.form("run_form")',
            source,
            "training controls must stay outside st.form so validation and Run disabled state rerender.",
        )

    def test_early_stopping_ui_rejects_zero(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn(
            'st.number_input("early_stopping_rounds", value=50, min_value=1)',
            source,
        )

    def test_run_button_is_disabled_by_training_guard_errors(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("validate_pipeline_training_inputs(", source)
        self.assertIn("disabled=(not provider_uri_valid or bool(guard_errors))", source)

    def test_saved_provider_picker_populates_provider_uri_state(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("list_provider_catalog_entries()", source)
        self.assertIn('"Saved data source"', source)
        self.assertIn('st.session_state["training_provider_uri"] = selected_entry.provider_uri', source)

    def test_config_page_exposes_delete_controls_for_jobs_and_saved_data(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("delete_provider_catalog_entry(selected_entry.job_id)", source)
        self.assertIn("JobManager.delete(job_id)", source)
        self.assertIn("disabled=status == \"running\" or not job_id", source)

    def test_training_dates_use_provider_trading_day_selectors(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("metadata.calendar_dates", source)
        self.assertIn("st.selectbox(", source)
        self.assertIn('"Only trading days from the selected provider calendar are selectable."', source)
        self.assertIn("_pipeline_date_defaults(provider_metadata)", source)
        self.assertIn("_walk_forward_date_defaults(provider_metadata)", source)

    def test_config_validation_errors_are_displayed_not_raised_raw(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("except (ValueError, JobManagerError) as exc:", source)
        self.assertIn("st.error(str(exc))", source)


if __name__ == "__main__":
    unittest.main()
