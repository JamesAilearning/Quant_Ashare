"""Unit tests for UI-only training launch guards."""

from __future__ import annotations

import json
import sys as _sys
import tempfile
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))


# Calendar with enough dates to leave a 2-trading-day embargo between
# adjacent segments. We deliberately keep the original 2025-09-30 →
# 2025-10-09 spacing (with no intervening trading days) so we can test
# the *failure* case for the embargo validator separately.
_PROVIDER_CALENDAR: tuple[str, ...] = (
    "2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08",
    "2025-01-09", "2025-01-10",
    "2025-06-26", "2025-06-27", "2025-06-30",
    "2025-07-01", "2025-07-02", "2025-07-03", "2025-07-04", "2025-07-07",
    "2025-09-26", "2025-09-29", "2025-09-30",
    "2025-10-09", "2025-10-10", "2025-10-13", "2025-10-14", "2025-10-15",
    "2025-12-25", "2025-12-26", "2025-12-29", "2025-12-30", "2025-12-31",
)


def _write_provider(root: Path) -> Path:
    provider = root / "qlib_provider"
    (provider / "calendars").mkdir(parents=True)
    (provider / "instruments").mkdir()
    (provider / "calendars" / "day.txt").write_text(
        "\n".join(_PROVIDER_CALENDAR), encoding="utf-8",
    )
    (provider / "instruments" / "all.txt").write_text(
        "SH600000\t2025-01-02\t2025-12-31\n", encoding="utf-8"
    )
    (root / "validation.json").write_text(
        json.dumps({
            "health": "ok",
            "coverage_start_date": "2025-01-02",
            "coverage_end_date": "2025-12-31",
            "calendar_count": len(_PROVIDER_CALENDAR),
            "instrument_count": 1,
            "row_count": len(_PROVIDER_CALENDAR),
        }),
        encoding="utf-8",
    )
    return provider


class OperatorUiTrainingGuardTests(unittest.TestCase):
    def test_provider_metadata_reads_adjacent_validation_and_provider_files(self) -> None:
        from web.operator_ui.training_guards import inspect_provider_metadata, provider_metadata_summary

        with tempfile.TemporaryDirectory() as tmp:
            provider = _write_provider(Path(tmp))
            metadata = inspect_provider_metadata(str(provider))

        self.assertEqual(str(metadata.coverage_start_date), "2025-01-02")
        self.assertEqual(str(metadata.coverage_end_date), "2025-12-31")
        self.assertEqual(metadata.health, "ok")
        self.assertEqual(metadata.instrument_universes, ("all",))
        summary = provider_metadata_summary(metadata)
        self.assertEqual(summary["coverage"], "2025-01-02 to 2025-12-31")
        self.assertEqual(summary["health"], "ok")

    def test_pipeline_guard_rejects_overlapping_train_and_valid_dates(self) -> None:
        from web.operator_ui.training_guards import validate_pipeline_training_inputs

        with tempfile.TemporaryDirectory() as tmp:
            provider = _write_provider(Path(tmp))
            result = validate_pipeline_training_inputs(
                provider_uri=str(provider),
                instruments="all",
                train_start="2025-01-02",
                train_end="2025-06-30",
                valid_start="2025-01-06",  # before train_end on purpose
                valid_end="2025-09-30",
                test_start="2025-10-13",
                test_end="2025-12-30",
            )

        self.assertFalse(result.ok)
        self.assertTrue(any("valid_start 必须严格晚于 train_end" in item for item in result.errors))

    def test_pipeline_guard_rejects_provider_final_trading_day_as_test_end(self) -> None:
        from web.operator_ui.training_guards import validate_pipeline_training_inputs

        with tempfile.TemporaryDirectory() as tmp:
            provider = _write_provider(Path(tmp))
            # Train and valid arranged with proper 2-day embargo so the
            # failure is unambiguously the tail-day overflow, not embargo.
            result = validate_pipeline_training_inputs(
                provider_uri=str(provider),
                instruments="all",
                train_start="2025-01-02",
                train_end="2025-01-03",
                valid_start="2025-01-08",
                valid_end="2025-12-25",
                test_start="2025-12-30",
                test_end="2025-12-31",  # provider's final trading day
            )

        self.assertFalse(result.ok)
        self.assertTrue(any("必须早于数据源最后一个交易日" in item for item in result.errors))
        self.assertTrue(any("test_end 设为 ≤ 2025-12-30" in item for item in result.errors))

    def test_pipeline_guard_warns_when_forward_buffer_is_short(self) -> None:
        from web.operator_ui.training_guards import validate_pipeline_training_inputs

        with tempfile.TemporaryDirectory() as tmp:
            provider = _write_provider(Path(tmp))
            result = validate_pipeline_training_inputs(
                provider_uri=str(provider),
                instruments="all",
                train_start="2025-01-02",
                train_end="2025-01-03",
                valid_start="2025-01-08",   # embargo: 01-06, 01-07
                valid_end="2025-09-26",
                test_start="2025-10-13",    # embargo across the holiday
                test_end="2025-12-30",
            )

        self.assertTrue(result.ok, f"unexpected errors: {result.errors}")
        self.assertTrue(any("20 日前向收益" in item for item in result.warnings))

    def test_pipeline_guard_rejects_missing_named_universe(self) -> None:
        from web.operator_ui.training_guards import validate_pipeline_training_inputs

        with tempfile.TemporaryDirectory() as tmp:
            provider = _write_provider(Path(tmp))
            result = validate_pipeline_training_inputs(
                provider_uri=str(provider),
                instruments="csi300",  # provider only has 'all'
                train_start="2025-01-02",
                train_end="2025-01-03",
                valid_start="2025-01-08",
                valid_end="2025-12-25",
                test_start="2025-12-30",
                test_end="2025-12-31",
            )

        self.assertFalse(result.ok)
        self.assertTrue(any("instruments='csi300' 不在数据源的" in item for item in result.errors))

    def test_is_non_production_ui_bundle_detection(self) -> None:
        """The detector flags ONLY a …/operator_ui/results/<job>/qlib_provider
        path, never a production bundle (U1 footgun guard)."""
        from web.operator_ui.training_guards import _is_non_production_ui_bundle

        self.assertTrue(_is_non_production_ui_bundle(
            Path("D:/x/output/operator_ui/results/job_123/qlib_provider")))
        # production bundle — not flagged
        self.assertFalse(_is_non_production_ui_bundle(
            Path("D:/qlib_data/my_cn_data_pit")))
        # a bare qlib_provider NOT under operator_ui/results — not flagged
        self.assertFalse(_is_non_production_ui_bundle(
            Path("D:/somewhere/qlib_provider")))
        self.assertFalse(_is_non_production_ui_bundle(None))

    def test_pipeline_guard_rejects_ui_results_bundle_as_training_source(self) -> None:
        """A non-production UI inspection bundle
        (…/operator_ui/results/<job>/qlib_provider) must be rejected as a
        training provider_uri — even when its dates/instruments are otherwise
        valid. A production bundle at the same date config passes."""
        from web.operator_ui.training_guards import validate_pipeline_training_inputs

        with tempfile.TemporaryDirectory() as tmp:
            ui_root = Path(tmp) / "output" / "operator_ui" / "results" / "job_x"
            provider = _write_provider(ui_root)  # ui_root/qlib_provider
            result = validate_pipeline_training_inputs(
                provider_uri=str(provider),
                instruments="all",
                train_start="2025-01-02",
                train_end="2025-01-03",
                valid_start="2025-01-08",
                valid_end="2025-09-26",
                test_start="2025-10-13",
                test_end="2025-12-30",
            )

        self.assertFalse(result.ok)
        self.assertTrue(
            any("非生产 bundle" in item for item in result.errors),
            f"expected non-production reject, got: {result.errors}",
        )

    def test_pipeline_guard_accepts_production_bundle_same_dates(self) -> None:
        """Control for the reject above: the SAME valid date config on a
        production-style bundle (NOT under operator_ui/results) passes."""
        from web.operator_ui.training_guards import validate_pipeline_training_inputs

        with tempfile.TemporaryDirectory() as tmp:
            provider = _write_provider(Path(tmp) / "my_cn_data_pit_root")
            result = validate_pipeline_training_inputs(
                provider_uri=str(provider),
                instruments="all",
                train_start="2025-01-02",
                train_end="2025-01-03",
                valid_start="2025-01-08",
                valid_end="2025-09-26",
                test_start="2025-10-13",
                test_end="2025-12-30",
            )

        self.assertTrue(result.ok, f"unexpected errors: {result.errors}")
        self.assertFalse(any("非生产 bundle" in item for item in result.errors))


class SegmentEmbargoTests(unittest.TestCase):
    """Regression tests for the label-lookahead embargo validator
    (PR6.4). Alpha158's default label consumes ``Ref($close, -2/-1)`` —
    without an embargo the last 2 rows of each non-test segment compute
    their labels from prices that fall inside the next segment, leaking
    information across the boundary.
    """

    def test_zero_embargo_between_valid_and_test_is_error(self) -> None:
        """Reproducer for the operator's pipeline_20260524_221821_b978a811
        run: valid_end=2025-09-30 → test_start=2025-10-09 with no trading
        days strictly between them. Embargo days = 0 < 2. Must reject."""

        from web.operator_ui.training_guards import validate_pipeline_training_inputs

        with tempfile.TemporaryDirectory() as tmp:
            provider = _write_provider(Path(tmp))
            result = validate_pipeline_training_inputs(
                provider_uri=str(provider),
                instruments="all",
                train_start="2025-01-02",
                train_end="2025-01-03",
                valid_start="2025-01-08",
                valid_end="2025-09-30",   # last validation day
                test_start="2025-10-09",  # next trading day — 0 embargo
                test_end="2025-10-15",
            )

        self.assertFalse(result.ok)
        self.assertTrue(
            any(
                "valid_end" in item and "test_start" in item and "embargo" in item
                for item in result.errors
            ),
            f"expected embargo error, got: {result.errors}",
        )

    def test_one_embargo_day_still_below_lookahead_is_error(self) -> None:
        from web.operator_ui.training_guards import validate_pipeline_training_inputs

        with tempfile.TemporaryDirectory() as tmp:
            provider = _write_provider(Path(tmp))
            # train_end=01-03 → valid_start=01-07 leaves only 01-06 in
            # between (1 trading day) — below LABEL_LOOKAHEAD_DAYS=2.
            result = validate_pipeline_training_inputs(
                provider_uri=str(provider),
                instruments="all",
                train_start="2025-01-02",
                train_end="2025-01-03",
                valid_start="2025-01-07",
                valid_end="2025-09-26",
                test_start="2025-10-13",
                test_end="2025-12-30",
            )

        self.assertFalse(result.ok)
        self.assertTrue(
            any(
                "train_end" in item and "valid_start" in item and "embargo" in item
                for item in result.errors
            ),
            f"expected embargo error, got: {result.errors}",
        )

    def test_two_embargo_days_passes(self) -> None:
        from web.operator_ui.training_guards import validate_pipeline_training_inputs

        with tempfile.TemporaryDirectory() as tmp:
            provider = _write_provider(Path(tmp))
            # train_end=01-03, valid_start=01-08 → embargo = [01-06, 01-07] = 2 days.
            # valid_end=09-26, test_start=10-13 → embargo crosses the
            # holiday, plenty of days. Both pass.
            result = validate_pipeline_training_inputs(
                provider_uri=str(provider),
                instruments="all",
                train_start="2025-01-02",
                train_end="2025-01-03",
                valid_start="2025-01-08",
                valid_end="2025-09-26",
                test_start="2025-10-13",
                test_end="2025-12-30",
            )

        # The tail-day warning may still fire, but no embargo error.
        self.assertFalse(
            any("embargo" in item for item in result.errors),
            f"unexpected embargo error: {result.errors}",
        )


class UniverseBenchmarkAlignmentTests(unittest.TestCase):
    """Regression tests for the universe/benchmark mismatch warning
    (PR6.4 B2). Picking instruments=all alongside benchmark=SH000300
    inflates the apparent excess return — operator should see a hint."""

    def test_all_universe_with_csi300_benchmark_warns(self) -> None:
        from web.operator_ui.training_guards import validate_pipeline_training_inputs

        with tempfile.TemporaryDirectory() as tmp:
            provider = _write_provider(Path(tmp))
            result = validate_pipeline_training_inputs(
                provider_uri=str(provider),
                instruments="all",
                train_start="2025-01-02",
                train_end="2025-01-03",
                valid_start="2025-01-08",
                valid_end="2025-09-26",
                test_start="2025-10-13",
                test_end="2025-12-30",
                benchmark_code="SH000300",
            )

        self.assertTrue(
            any("instruments=all" in w and "SH000300" in w for w in result.warnings),
            f"expected mismatch warning, got: {result.warnings}",
        )

    def test_csi300_universe_with_csi300_benchmark_no_warning(self) -> None:
        from web.operator_ui.training_guards import validate_pipeline_training_inputs

        with tempfile.TemporaryDirectory() as tmp:
            provider = _write_provider(Path(tmp))
            result = validate_pipeline_training_inputs(
                provider_uri=str(provider),
                instruments="csi300",
                train_start="2025-01-02",
                train_end="2025-01-03",
                valid_start="2025-01-08",
                valid_end="2025-09-26",
                test_start="2025-10-13",
                test_end="2025-12-30",
                benchmark_code="SH000300",
            )

        # csi300 universe matches SH000300 benchmark — no alignment warning
        # (instruments missing-universe error fires separately because the
        # test provider only has 'all.txt'; that's a different validator).
        self.assertFalse(
            any("不一致" in w or "不同口径" in w for w in result.warnings),
            f"unexpected alignment warning: {result.warnings}",
        )

    def test_unknown_benchmark_skips_alignment_check(self) -> None:
        from web.operator_ui.training_guards import validate_pipeline_training_inputs

        with tempfile.TemporaryDirectory() as tmp:
            provider = _write_provider(Path(tmp))
            result = validate_pipeline_training_inputs(
                provider_uri=str(provider),
                instruments="all",
                train_start="2025-01-02",
                train_end="2025-01-03",
                valid_start="2025-01-08",
                valid_end="2025-09-26",
                test_start="2025-10-13",
                test_end="2025-12-30",
                benchmark_code="SH600000",  # not in the hint table
            )

        # No heuristic data → no alignment warning.
        self.assertFalse(
            any("不一致" in w or "不同口径" in w for w in result.warnings),
            f"unexpected alignment warning: {result.warnings}",
        )


if __name__ == "__main__":
    unittest.main()
