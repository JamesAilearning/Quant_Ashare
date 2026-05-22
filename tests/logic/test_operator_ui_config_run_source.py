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

    def test_tushare_extracted_to_dedicated_page(self) -> None:
        """Tushare ingestion SHALL live on its own page (pages/tushare.py)
        so model-run config never mixes with data-ingestion controls
        (TICKET-C polish — Codex review on PR1)."""

        config_run = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")
        tushare_page = Path("web/operator_ui/pages/tushare.py")

        self.assertTrue(tushare_page.is_file(), "pages/tushare.py SHALL exist")
        tushare_src = tushare_page.read_text(encoding="utf-8")

        # The dedicated page owns the form and the token gating.
        self.assertIn('st.form("tushare_provider_form")', tushare_src)
        self.assertIn('TUSHARE_TOKEN', tushare_src)
        self.assertIn('JobManager.start', tushare_src)
        self.assertIn('"tushare_provider"', tushare_src)

        # Config & Run no longer carries Tushare-specific imports or wiring.
        self.assertNotIn('st.form("tushare_provider_form")', config_run)
        self.assertNotIn('TUSHARE_PROVIDER_KEYS', config_run)
        self.assertNotIn('ADJUST_MODE_PRE', config_run)
        self.assertNotIn('"tushare_provider"', config_run)

    def test_tushare_token_never_appears_in_yaml_or_preview(self) -> None:
        """Hard rule: the token is environment-only and SHALL NOT appear
        in any YAML preview, st.code rendering, or persisted state on the
        Tushare page (project secrets policy)."""

        tushare_src = Path("web/operator_ui/pages/tushare.py").read_text(encoding="utf-8")

        # The page reads the env var to gate, but never copies it into
        # st.code / st.text / config dict / session_state.
        self.assertNotIn("st.code(", tushare_src)
        self.assertNotIn('TUSHARE_TOKEN"]', tushare_src.replace("os.environ.get(", "X"))
        # Token is referenced only via os.environ.get(... ).strip() bool check.
        self.assertIn('os.environ.get("TUSHARE_TOKEN"', tushare_src)
        # Token name SHALL NOT show up in any user-facing value:
        # the only allowed occurrences are env-var reference + caption /
        # warning copy. None of them write the token's value anywhere.

    def test_app_nav_includes_tushare_page(self) -> None:
        """The Tushare page SHALL be reachable from the sidebar nav."""

        app_src = Path("web/operator_ui/app.py").read_text(encoding="utf-8")
        self.assertIn('tushare.py', app_src)
        self.assertIn('"Tushare Data"', app_src)

    def test_yaml_preview_offers_copy_and_diff(self) -> None:
        """The YAML preview pane SHALL surface a Copy button and a
        ``Show diff vs preset`` toggle (TICKET-C polish)."""

        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("📋 Copy YAML", source)
        self.assertIn("cr_copy_yaml_btn", source)
        self.assertIn("cr_show_diff_toggle", source)
        self.assertIn("Show diff vs preset", source)
        # Diff is computed via stdlib difflib against the active preset.
        self.assertIn("difflib", source)
        self.assertIn("unified_diff", source)
        # The toast confirms the copy action.
        self.assertIn('st.toast("YAML copied', source)

    def test_guard_errors_surface_auto_fix_buttons(self) -> None:
        """When a guard error has a known mechanical resolution, the
        status panel SHALL render a one-click fix alongside it."""

        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("auto_fixes", source)
        # The GPU + non-LGBModel combo is the canonical example registered
        # in this PR; its fix label is documented here so the auto-fix
        # plumbing has at least one concrete attach point.
        self.assertIn("Switch model → LGBModel", source)
        self.assertIn("_fix_gpu_model", source)

    def test_pipeline_dates_offer_quick_range_presets(self) -> None:
        """The pipeline date block SHALL surface quick range buttons
        (Full history / Last 5y / Last 3y / Reset to preset)."""

        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("Quick date range presets", source)
        self.assertIn("Full history", source)
        self.assertIn("Last 5y", source)
        self.assertIn("Last 3y", source)
        self.assertIn("Reset to preset", source)
        self.assertIn("_last_n_days_split(", source)


class LastNDaysSplitTests(unittest.TestCase):
    def test_returns_none_for_empty_calendar(self) -> None:
        import types

        from web.operator_ui.pages.config_run import _last_n_days_split

        metadata = types.SimpleNamespace(calendar_dates=())
        self.assertIsNone(_last_n_days_split(metadata, 252 * 5))

    def test_returns_none_for_undersized_calendar(self) -> None:
        import types
        from datetime import date as _d

        from web.operator_ui.pages.config_run import _last_n_days_split

        # Below the 50-day floor: return None rather than guess.
        cal = tuple(_d(2026, 1, 1).fromordinal(_d(2026, 1, 1).toordinal() + i) for i in range(20))
        metadata = types.SimpleNamespace(calendar_dates=cal)
        self.assertIsNone(_last_n_days_split(metadata, 252 * 5))

    def test_split_produces_six_monotone_dates(self) -> None:
        import types
        from datetime import date as _d

        from web.operator_ui.pages.config_run import _last_n_days_split

        # Build a 1000-day synthetic calendar (real provider would be a
        # trading-day calendar; the helper doesn't care about gaps).
        base = _d(2020, 1, 1)
        cal = tuple(_d.fromordinal(base.toordinal() + i) for i in range(1000))
        metadata = types.SimpleNamespace(calendar_dates=cal)

        result = _last_n_days_split(metadata, 252 * 5)  # 1260 -> capped to 1000
        self.assertIsNotNone(result)
        assert result is not None  # for type-checker
        ordered = [
            result["train_start"], result["train_end"],
            result["valid_start"], result["valid_end"],
            result["test_start"], result["test_end"],
        ]
        # Monotone non-decreasing.
        for earlier, later in zip(ordered, ordered[1:]):
            self.assertLessEqual(earlier, later)
        # Train/Valid/Test ranges are non-empty.
        self.assertLess(result["train_start"], result["train_end"])
        self.assertLess(result["valid_start"], result["valid_end"])
        self.assertLess(result["test_start"], result["test_end"])


if __name__ == "__main__":
    unittest.main()
