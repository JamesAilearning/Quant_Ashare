"""Source-level regression guards for Streamlit Config & Run wiring."""

from __future__ import annotations

import unittest
from pathlib import Path

# Some CI cells (ubuntu-3.10 / ubuntu-3.12) install streamlit via a step that
# has ``continue-on-error: true`` and may or may not succeed.  Source-level
# tests (read .py as text) don't need streamlit, but the LastNDaysSplitTests
# class imports a function from a page module that loads ``streamlit`` at
# import time.  Skip that class cleanly rather than fail the cell.
try:
    import streamlit as _streamlit  # noqa: F401

    _HAS_STREAMLIT = True
except ImportError:
    _HAS_STREAMLIT = False


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

    def test_jobs_page_references_job_manager(self) -> None:
        source = Path("web/operator_ui/pages/jobs.py").read_text(encoding="utf-8")

        self.assertIn("list_all_jobs", source)
        page_imports_jobs = "from web.operator_ui.job_io" in source or "JobManager" in source
        self.assertTrue(page_imports_jobs, "jobs.py should import from job_io or JobManager")

    def test_training_dates_use_provider_trading_day_selectors(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("metadata.calendar_dates", source)
        self.assertIn("st.selectbox(", source)
        self.assertIn('"仅可在所选数据源日历内的交易日中选择。"', source)
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

    def test_config_dict_injects_namechange_path_for_both_modes(self) -> None:
        """PR-F (audit E1): the official single-fold AND walk-forward
        backtest paths now hard-require a non-empty ``namechange_path``
        (``require_st_mask=True``). The UI emits a STANDALONE job config
        (no ``extends`` / no loader env-expansion), so the page MUST
        inject the env-defaulted path into ``config_dict`` BEFORE the
        mode split's preview/validation — covering pipeline and
        walk_forward alike — or a UI-launched run RAISES after a full
        train."""

        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("resolve_namechange_path", source)
        self.assertIn(
            'config_dict.setdefault("namechange_path", resolve_namechange_path())',
            source,
        )
        # The injection must sit AFTER both mode branches set known_keys
        # (so both modes are covered) and BEFORE the preview is built.
        inject_at = source.index('config_dict.setdefault("namechange_path"')
        wf_branch_at = source.index("known_keys = WALK_FORWARD_KEYS")
        preview_at = source.index('preview_config = {"mode": mode, **config_dict}')
        self.assertLess(wf_branch_at, inject_at, "inject must follow the mode split")
        self.assertLess(inject_at, preview_at, "inject must precede the preview")

    def test_preset_yaml_files_exist(self) -> None:
        presets_dir = Path("config/presets")
        for name in ("smoke", "default", "production"):
            self.assertTrue(
                (presets_dir / f"{name}.yaml").is_file(),
                f"Missing preset: {name}.yaml",
            )

    def test_yaml_preview_offers_copy_and_diff(self) -> None:
        """The YAML preview pane SHALL surface a Copy button and a
        ``Show diff vs preset`` toggle (TICKET-C polish)."""

        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("📋 复制 YAML", source)
        self.assertIn("cr_copy_yaml_btn", source)
        self.assertIn("cr_show_diff_toggle", source)
        self.assertIn("与预设差异对比", source)
        # Diff is computed via stdlib difflib against the active preset.
        self.assertIn("difflib", source)
        self.assertIn("unified_diff", source)
        # The toast confirms the copy action.
        self.assertIn('st.toast("已复制 YAML', source)

    def test_guard_errors_surface_auto_fix_buttons(self) -> None:
        """When a guard error has a known mechanical resolution, the
        status panel SHALL render a one-click fix alongside it."""

        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("auto_fixes", source)
        # The GPU + non-LGBModel combo is the canonical example registered
        # in this PR; its fix label is documented here so the auto-fix
        # plumbing has at least one concrete attach point.
        self.assertIn("切换为 LGBModel", source)
        self.assertIn("_fix_gpu_model", source)

    def test_pipeline_dates_offer_quick_range_presets(self) -> None:
        """The pipeline date block SHALL surface quick range buttons
        (Full history / Last 5y / Last 3y / Reset to preset)."""

        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("日期范围快捷预设", source)
        self.assertIn("全部历史", source)
        self.assertIn("最近 5 年", source)
        self.assertIn("最近 3 年", source)
        self.assertIn("重置为预设值", source)
        self.assertIn("_last_n_days_split(", source)


@unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed in this CI cell")
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
        # ``strict=False`` because zipping ``[1..6]`` with ``[2..6]`` deliberately
        # has unequal lengths — we want all consecutive pairs.
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            self.assertLessEqual(earlier, later)
        # Train/Valid/Test ranges are non-empty.
        self.assertLess(result["train_start"], result["train_end"])
        self.assertLess(result["valid_start"], result["valid_end"])
        self.assertLess(result["test_start"], result["test_end"])

    def test_split_leaves_embargo_between_segments(self) -> None:
        """Regression for Codex PR6.4 P1: the embargo guard in
        training_guards.py rejects splits with < LABEL_LOOKAHEAD_DAYS=2
        trading days between train_end → valid_start and valid_end →
        test_start. The quick presets must produce ranges that satisfy
        the guard so clicking ``最近 5 年`` doesn't immediately disable
        the Run button.
        """

        import types
        from datetime import date as _d

        from web.operator_ui.pages.config_run import _last_n_days_split
        from web.operator_ui.training_guards import LABEL_LOOKAHEAD_DAYS

        base = _d(2020, 1, 1)
        cal = tuple(_d.fromordinal(base.toordinal() + i) for i in range(1000))
        metadata = types.SimpleNamespace(calendar_dates=cal)
        result = _last_n_days_split(metadata, 252 * 5)
        assert result is not None

        # Count trading days strictly between each boundary pair.
        cal_set = set(cal)
        ts = {key: _d.fromisoformat(result[key]) for key in result}

        def _gap(earlier: _d, later: _d) -> int:
            return sum(1 for day in cal if earlier < day < later)

        # ``cal_set`` is only used to assert each preset date is itself in
        # the calendar — that the helper picked snapped trading days.
        for key, value in ts.items():
            self.assertIn(value, cal_set, f"preset {key} ({value}) is not in calendar")

        self.assertGreaterEqual(
            _gap(ts["train_end"], ts["valid_start"]),
            LABEL_LOOKAHEAD_DAYS,
            f"train_end→valid_start embargo too small: {result}",
        )
        self.assertGreaterEqual(
            _gap(ts["valid_end"], ts["test_start"]),
            LABEL_LOOKAHEAD_DAYS,
            f"valid_end→test_start embargo too small: {result}",
        )


@unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed in this CI cell")
class SixIncreasingIndicesTests(unittest.TestCase):
    """Regression for Codex PR6.4 P1: the long-default preset
    (`_pipeline_date_defaults` → `_six_increasing_indices`) must also
    leave enough room on each segment boundary so the embargo validator
    doesn't block ``全部历史`` clicks."""

    def test_indices_leave_embargo_at_segment_boundaries(self) -> None:
        from web.operator_ui.pages.config_run import _six_increasing_indices
        from web.operator_ui.training_guards import LABEL_LOOKAHEAD_DAYS

        indices = _six_increasing_indices(500)
        # Boundary 1: train_end → valid_start
        self.assertGreaterEqual(
            indices[2] - indices[1] - 1, LABEL_LOOKAHEAD_DAYS,
            f"train_end→valid_start gap too small in {indices}",
        )
        # Boundary 2: valid_end → test_start
        self.assertGreaterEqual(
            indices[4] - indices[3] - 1, LABEL_LOOKAHEAD_DAYS,
            f"valid_end→test_start gap too small in {indices}",
        )
        # Non-boundary pairs still strictly increasing.
        for i in range(5):
            self.assertLess(indices[i], indices[i + 1])
        # Indices fit within [0, last_index].
        self.assertEqual(indices[0], 0)
        self.assertLessEqual(indices[-1], 500)

    def test_returns_compact_layout_when_calendar_too_short(self) -> None:
        """Very short calendar can't satisfy embargos. Helper returns a
        best-effort layout rather than crashing; the embargo validator
        will then surface the real error to the operator."""

        from web.operator_ui.pages.config_run import _six_increasing_indices

        indices = _six_increasing_indices(3)
        self.assertEqual(len(indices), 6)
        # All indices are in [0, last_index] and the layout is monotone
        # non-decreasing (the helper clips rather than synthesising).
        self.assertTrue(all(0 <= i <= 3 for i in indices))


@unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed in this CI cell")
class SelectTradingDayFallbackTests(unittest.TestCase):
    """``_select_trading_day`` SHALL NOT silently snap a configured
    default to the calendar's first entry when the default falls outside
    the provider's calendar.

    The previous ``_option_index`` returned 0 on miss so the page just
    swapped, say, ``train_start=2022-01-01`` for ``calendar[0]=2023-06-12``
    with zero visual signal. Operators chased "why did my run skip 2022?"
    ghosts for days. UI review P1-9 made ``_option_index`` return -1 on
    miss; ``_select_trading_day`` then snaps to index 0 BUT surfaces
    ``st.warning`` so the operator sees the date change.
    """

    def test_option_index_returns_negative_one_on_miss(self) -> None:
        from web.operator_ui.pages.config_run import _option_index

        options = ["2023-01-03", "2023-01-04", "2023-01-05"]
        self.assertEqual(_option_index(options, "2022-12-01"), -1)
        # Hit still returns the real index.
        self.assertEqual(_option_index(options, "2023-01-04"), 1)
        # Empty options also returns -1 (defensive).
        self.assertEqual(_option_index([], "anything"), -1)

    def test_select_trading_day_warns_when_default_outside_calendar(self) -> None:
        """When the default falls outside the calendar, the page MUST
        emit a visible ``st.warning`` mentioning both the missing
        default and the replacement value."""

        import types
        from datetime import date as _d
        from unittest.mock import patch

        from web.operator_ui.pages import config_run

        cal = (
            _d(2023, 6, 12),
            _d(2023, 6, 13),
            _d(2023, 6, 14),
        )
        metadata = types.SimpleNamespace(calendar_dates=cal)

        captured_warnings: list[str] = []
        with patch(
            "streamlit.warning",
            side_effect=lambda msg, *_a, **_kw: captured_warnings.append(msg),
        ), patch(
            "streamlit.selectbox",
            side_effect=lambda label, options, **kw: options[kw.get("index", 0)],
        ):
            result = config_run._select_trading_day(
                "train_start",
                default="2022-01-01",
                metadata=metadata,
            )

        # Snapped to calendar[0].
        self.assertEqual(result, "2023-06-12")
        # Exactly one warning, mentioning both the bad default and
        # the replacement.
        self.assertEqual(len(captured_warnings), 1)
        warning = captured_warnings[0]
        self.assertIn("train_start", warning)
        self.assertIn("2022-01-01", warning)
        self.assertIn("2023-06-12", warning)

    def test_select_trading_day_does_not_warn_when_default_is_in_calendar(self) -> None:
        """Hit path stays silent — the warning is for the silent-snap
        case only, not a generic "you used a preset" reminder."""

        import types
        from datetime import date as _d
        from unittest.mock import patch

        from web.operator_ui.pages import config_run

        cal = (_d(2023, 6, 12), _d(2023, 6, 13), _d(2023, 6, 14))
        metadata = types.SimpleNamespace(calendar_dates=cal)

        captured_warnings: list[str] = []
        with patch(
            "streamlit.warning",
            side_effect=lambda msg, *_a, **_kw: captured_warnings.append(msg),
        ), patch(
            "streamlit.selectbox",
            side_effect=lambda label, options, **kw: options[kw.get("index", 0)],
        ):
            result = config_run._select_trading_day(
                "train_start",
                default="2023-06-13",
                metadata=metadata,
            )

        self.assertEqual(result, "2023-06-13")
        self.assertEqual(captured_warnings, [])

    def test_select_trading_day_no_calendar_falls_back_to_text_input(self) -> None:
        """When the provider exposes no calendar at all (no metadata or
        empty calendar), the helper degrades to ``st.text_input`` and
        does NOT warn — the snap-warning is specifically about
        out-of-calendar defaults, not unconfigured providers."""

        import types
        from unittest.mock import patch

        from web.operator_ui.pages import config_run

        metadata = types.SimpleNamespace(calendar_dates=())

        captured_warnings: list[str] = []
        with patch(
            "streamlit.warning",
            side_effect=lambda msg, *_a, **_kw: captured_warnings.append(msg),
        ), patch(
            "streamlit.text_input",
            side_effect=lambda label, value: value,
        ):
            result = config_run._select_trading_day(
                "train_start",
                default="2022-01-01",
                metadata=metadata,
            )

        self.assertEqual(result, "2022-01-01")
        self.assertEqual(captured_warnings, [])


class AutoFixWidgetKeyStabilityTests(unittest.TestCase):
    """UI review P2-10: the auto-fix button's widget key used
    ``abs(hash(err)) % 10_000_000``. Python's ``hash()`` of a str
    varies per process (PYTHONHASHSEED), so a server restart re-keyed
    the button and orphaned any session_state tied to the old key. The
    key now derives from a stable ``hashlib.md5`` content digest."""

    def test_auto_fix_key_uses_stable_content_hash_not_builtin_hash(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("import hashlib", source)
        self.assertIn("hashlib.md5(", source)
        # The process-varying builtin-hash key form must be gone.
        self.assertNotIn("abs(hash(err))", source)
        self.assertNotIn("hash(err) %", source)
        # md5 here is a non-security content digest — flagged so a
        # security linter / FIPS build doesn't choke.
        self.assertIn("usedforsecurity=False", source)


class RenderFieldFootgunDocTests(unittest.TestCase):
    """UI review P2-13: ``render_field`` emits ``control_html`` verbatim.
    Today all call sites pass static literals so there's no live XSS,
    but the docstring MUST warn so a future caller interpolating
    operator / artifact data escapes it first."""

    def test_render_field_docstring_warns_about_unescaped_control_html(self) -> None:
        source = Path("web/operator_ui/components.py").read_text(encoding="utf-8")

        func_start = source.index("def render_field(")
        # Scope to the function's docstring region.
        body = source[func_start:func_start + 1200]
        self.assertIn("verbatim", body)
        self.assertIn("XSS footgun", body)
        self.assertIn("P2-13", body)


@unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed in this CI cell")
class EstimateCalibrationTests(unittest.TestCase):
    """UI review P2-6: the duration estimate uses an empirical
    seconds-per-work-unit rate from recent jobs when available, falling
    back to the hardcoded throughput constants otherwise."""

    def test_work_units_scale_with_config_drivers(self) -> None:
        from web.operator_ui.pages.config_run import _pipeline_work_units

        small = {"instruments": "csi300", "num_boost_round": 1000,
                 "train_start": "2024-01-01", "train_end": "2024-12-31"}
        big = {"instruments": "all", "num_boost_round": 2000,
               "train_start": "2020-01-01", "train_end": "2024-12-31"}
        self.assertGreater(_pipeline_work_units(big), _pipeline_work_units(small))

    def test_calibration_returns_median_rate(self) -> None:
        from web.operator_ui.pages.config_run import (
            _calibration_seconds_per_unit,
            _pipeline_work_units,
        )

        cfg = {"instruments": "csi300", "num_boost_round": 1000,
               "train_start": "2024-01-01", "train_end": "2024-12-31"}
        units = _pipeline_work_units(cfg)
        # Three samples with rates 2, 4, 6 sec/unit → median 4.
        samples = [(cfg, units * 2), (cfg, units * 4), (cfg, units * 6)]
        rate = _calibration_seconds_per_unit(samples)
        self.assertIsNotNone(rate)
        assert rate is not None
        self.assertAlmostEqual(rate, 4.0, places=6)

    def test_calibration_none_for_empty_or_invalid_samples(self) -> None:
        from web.operator_ui.pages.config_run import _calibration_seconds_per_unit

        self.assertIsNone(_calibration_seconds_per_unit([]))
        # Non-positive durations are dropped.
        self.assertIsNone(_calibration_seconds_per_unit([({"x": 1}, 0.0)]))

    def test_estimate_uses_calibration_when_provided(self) -> None:
        from web.operator_ui.pages.config_run import (
            _estimate_duration,
            _pipeline_work_units,
        )

        cfg = {"instruments": "csi300", "num_boost_round": 1000,
               "train_start": "2024-01-01", "train_end": "2024-12-31"}
        units = _pipeline_work_units(cfg)
        # Calibrate to exactly 1 hour: rate so that units*rate = 3600s.
        rate = 3600.0 / units
        out = _estimate_duration(cfg, seconds_per_unit=rate)
        self.assertEqual(out, "约 1 小时 0 分")

    def test_estimate_falls_back_to_formula_without_calibration(self) -> None:
        from web.operator_ui.pages.config_run import _estimate_duration

        cfg = {"instruments": "csi300", "compute_device": "cpu",
               "num_boost_round": 1000,
               "train_start": "2024-01-01", "train_end": "2024-12-31"}
        out = _estimate_duration(cfg, seconds_per_unit=None)
        self.assertTrue(out.startswith("约 "))

    def test_config_page_wires_calibration_into_estimate(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")
        self.assertIn("_gather_calibration_seconds_per_unit()", source)
        self.assertIn("seconds_per_unit=_calibration_rate", source)


class JobsCleanupUiSourceTests(unittest.TestCase):
    """UI review P2-11: the jobs page exposes a one-click bulk cleanup
    section gated behind an explicit confirmation."""

    def test_jobs_page_wires_cleanup_section(self) -> None:
        source = Path("web/operator_ui/pages/jobs.py").read_text(encoding="utf-8")
        self.assertIn("🧹 清理旧作业", source)
        self.assertIn("jobs_eligible_for_cleanup(", source)
        # Two-step: confirm checkbox gates the delete button.
        self.assertIn('key="jobs_cleanup_confirm"', source)
        self.assertIn("disabled=not confirm", source)
        self.assertIn("JobManager.delete(run_id)", source)


class WalkForwardLogFilterSourceTests(unittest.TestCase):
    """UI review P2-12: the walk-forward log tab gains the same search +
    severity filter the pipeline results log tab already had."""

    def test_walk_forward_log_tab_has_search_and_level_filter(self) -> None:
        source = Path("web/operator_ui/pages/walk_forward.py").read_text(encoding="utf-8")
        self.assertIn("from web.operator_ui.result_view_helpers import", source)
        self.assertIn("filter_log_text", source)
        self.assertIn('key="wf_log_search"', source)
        self.assertIn('key="wf_log_levels"', source)
        # The plain unfiltered ``st.code(text …）`` dump is gone.
        self.assertNotIn('st.code(text or "（空）"', source)


class ProviderUriPrefillTests(unittest.TestCase):
    """provider_uri must prefill the canonical default (config.yaml
    ${QUANT_PROVIDER_URI:-…}) like the 数据检视 page, and stop nudging operators
    at the legacy NON-PIT bundle via the placeholder."""

    def setUp(self) -> None:
        self.source = Path(
            "web/operator_ui/pages/config_run.py"
        ).read_text(encoding="utf-8")

    def test_imports_default_provider_resolver(self) -> None:
        self.assertIn(
            "from web.operator_ui.bundle_health import "
            "resolve_default_provider_uri",
            self.source,
        )

    def test_provider_uri_prefilled_from_resolved_default(self) -> None:
        self.assertIn(
            '_cr("provider_uri", resolve_default_provider_uri()', self.source
        )

    def test_legacy_non_pit_placeholder_gone(self) -> None:
        # Exact old placeholder (the legacy non-PIT bundle) must be removed;
        # the new one references the PIT bundle / QUANT_PROVIDER_URI.
        self.assertNotIn('placeholder="D:/qlib_data/my_cn_data"', self.source)
        self.assertIn("QUANT_PROVIDER_URI", self.source)


class PresetSaveStripsMachineLocalPathsTests(unittest.TestCase):
    """Saving a preset must NOT bake machine-local paths (provider_uri,
    namechange_path) into the YAML — the tracked built-ins omit them, and a
    saved inspection-bundle provider_uri gets the preset rejected at launch."""

    def setUp(self) -> None:
        self.source = Path(
            "web/operator_ui/pages/config_run.py"
        ).read_text(encoding="utf-8")

    def test_save_excludes_machine_local_paths(self) -> None:
        self.assertIn(
            'if k not in ("provider_uri", "namechange_path")', self.source
        )

    def test_save_does_not_dump_raw_preview_config(self) -> None:
        # The verbatim dump (which baked provider_uri) must be gone from save.
        self.assertNotIn("yaml.dump(preview_config,", self.source)


class WalkForwardLaunchParityTests(unittest.TestCase):
    """The walk-forward guard branch must run the universe/benchmark mismatch
    warning the pipeline path runs (instruments=all vs a major index inflates
    "excess vs benchmark"). UI-audit follow-up.

    (The sibling WF-date preset/prefill fix was reverted: routing the dates
    through _cr regressed provider-calendar tracking — codex P2 on #300 — and a
    correct fix needs runtime verification. The dates stay on the live default.)
    """

    def setUp(self) -> None:
        self.source = Path(
            "web/operator_ui/pages/config_run.py"
        ).read_text(encoding="utf-8")

    def test_wf_dates_stay_on_live_provider_default(self) -> None:
        # Provider-tracking raw default (NOT _cr) — reverted per codex P2.
        self.assertIn(
            'default=walk_forward_date_defaults["overall_start"]', self.source
        )
        self.assertIn(
            'default=walk_forward_date_defaults["overall_end"]', self.source
        )

    def test_wf_branch_runs_universe_benchmark_alignment(self) -> None:
        self.assertIn("_validate_universe_benchmark_alignment(", self.source)
        self.assertIn(
            "instruments, benchmark_code, guard_warnings", self.source
        )


if __name__ == "__main__":
    unittest.main()
