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

        self.assertIn('"early_stopping_rounds"', source)
        self.assertIn('min_value=1', source)
        self.assertIn(
            'early_stopping_rounds',
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
        self.assertIn('cr_provider_uri', source)

    def test_config_page_exposes_delete_controls_for_saved_data(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("delete_provider_catalog_entry(selected_entry.job_id)", source)

    def test_jobs_page_references_job_manager(self) -> None:
        source = Path("web/operator_ui/pages/jobs.py").read_text(encoding="utf-8")

        self.assertIn("list_all_jobs", source)
        page_imports_jobs = "from web.operator_ui.job_io" in source or "JobManager" in source
        self.assertTrue(page_imports_jobs, "jobs.py should import from job_io or JobManager")

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

    def test_config_page_consumes_rerun_prefill_without_provider_value_binding(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("prefill_config_yaml", source)
        self.assertIn("yaml.safe_load", source)
        self.assertIn('cr_provider_uri', source)
        self.assertIn("prefill_config_applied_token", source)

    def test_config_page_has_preset_system(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("_preset_options", source)
        self.assertIn("list_preset_names", source)
        self.assertIn("_apply_preset", source)
        self.assertIn("_detect_preset", source)
        self.assertIn('"Custom"', source)

    def test_config_page_initializes_default_preset_fields(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn('"cr_preset_initialized"', source)
        self.assertIn('_apply_preset("Default")', source)
        self.assertIn('value=_cr("instruments", "csi300")', source)
        self.assertIn('value=_cr("feature_handler", "Alpha158")', source)

    def test_runtime_config_excludes_ui_mode_key(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("if submitted:", source)
        self.assertIn('preview_config = {"mode": mode, **config_dict}', source)
        self.assertIn("validate_config_keys(config_dict, known_keys)", source)
        self.assertIn("JobManager.start(config_dict, mode)", source)
        self.assertNotIn("validate_config_keys(preview_config", source)
        self.assertNotIn("JobManager.start(preview_config", source)

    def test_preset_yaml_files_exist(self) -> None:
        presets_dir = Path("config/presets")
        for name in ("smoke", "default", "production"):
            self.assertTrue(
                (presets_dir / f"{name}.yaml").is_file(),
                f"Missing preset: {name}.yaml",
            )


if __name__ == "__main__":
    unittest.main()
