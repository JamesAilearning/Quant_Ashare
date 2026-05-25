"""Tests for the shared Alpha158 label-lookahead embargo validator
and its integration into ``FeatureDatasetBuilder._validate``.

The audit found that the embargo check lived only in the UI
(``web/operator_ui/training_guards._validate_segment_embargo``), so
the CLI / main.py / direct API path bypassed it entirely — leaky
configs silently produced inflated OOS metrics. This file pins both
the shared validator and the core builder's new mandatory call.
"""

from __future__ import annotations

import logging
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data._segment_embargo import (  # noqa: E402
    LABEL_LOOKAHEAD_DAYS,
    trading_days_between,
    validate_segment_embargo,
)
from src.data.feature_dataset_builder import (  # noqa: E402
    FeatureDatasetBuilder,
    FeatureDatasetBuilderError,
    FeatureDatasetConfig,
)


def _make_calendar(start: date, days: int) -> list[date]:
    """Return ``days`` consecutive calendar days (no weekend skipping —
    sufficient for the validator's exclusive-count semantics)."""
    return [start + timedelta(days=i) for i in range(days)]


# ---------------------------------------------------------------------------
# trading_days_between — pure
# ---------------------------------------------------------------------------


class TradingDaysBetweenTests(unittest.TestCase):
    def test_later_equal_or_before_returns_zero(self):
        cal = _make_calendar(date(2024, 1, 1), 30)
        self.assertEqual(trading_days_between(date(2024, 1, 5), date(2024, 1, 5), cal), 0)
        self.assertEqual(trading_days_between(date(2024, 1, 5), date(2024, 1, 4), cal), 0)

    def test_adjacent_days_zero_gap(self):
        cal = _make_calendar(date(2024, 1, 1), 30)
        # 2024-01-05 and 2024-01-06 are adjacent trading days; nothing
        # strictly between them.
        self.assertEqual(trading_days_between(date(2024, 1, 5), date(2024, 1, 6), cal), 0)

    def test_one_day_between(self):
        cal = _make_calendar(date(2024, 1, 1), 30)
        self.assertEqual(trading_days_between(date(2024, 1, 5), date(2024, 1, 7), cal), 1)

    def test_empty_calendar_returns_zero(self):
        self.assertEqual(trading_days_between(date(2024, 1, 1), date(2024, 12, 31), []), 0)


# ---------------------------------------------------------------------------
# validate_segment_embargo — pure
# ---------------------------------------------------------------------------


class ValidateSegmentEmbargoTests(unittest.TestCase):
    def _good_window(self):
        # 100 days of calendar starting 2024-01-01
        cal = _make_calendar(date(2024, 1, 1), 100)
        return cal, dict(
            train_end=date(2024, 1, 10),
            valid_start=date(2024, 1, 15),  # 4 days gap → plenty
            valid_end=date(2024, 1, 25),
            test_start=date(2024, 1, 30),  # 4 days gap → plenty
        )

    def test_clean_windows_no_errors(self):
        cal, win = self._good_window()
        errors = validate_segment_embargo(
            **win, calendar=cal, lookahead_days=LABEL_LOOKAHEAD_DAYS,
        )
        self.assertEqual(errors, [])

    def test_adjacent_train_valid_flagged(self):
        cal = _make_calendar(date(2024, 1, 1), 100)
        errors = validate_segment_embargo(
            train_end=date(2024, 1, 10),
            valid_start=date(2024, 1, 11),  # 0-day gap → leak
            valid_end=date(2024, 1, 25),
            test_start=date(2024, 1, 30),
            calendar=cal,
            lookahead_days=2,
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("train_end", errors[0])
        self.assertIn("valid_start", errors[0])
        self.assertIn("0 trading day", errors[0])

    def test_adjacent_valid_test_flagged(self):
        cal = _make_calendar(date(2024, 1, 1), 100)
        errors = validate_segment_embargo(
            train_end=date(2024, 1, 10),
            valid_start=date(2024, 1, 15),
            valid_end=date(2024, 1, 25),
            test_start=date(2024, 1, 26),  # 0-day gap → leak
            calendar=cal,
            lookahead_days=2,
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("valid_end", errors[0])
        self.assertIn("test_start", errors[0])

    def test_both_pairs_flagged_in_stable_order(self):
        cal = _make_calendar(date(2024, 1, 1), 100)
        errors = validate_segment_embargo(
            train_end=date(2024, 1, 10),
            valid_start=date(2024, 1, 11),
            valid_end=date(2024, 1, 12),
            test_start=date(2024, 1, 13),
            calendar=cal,
            lookahead_days=2,
        )
        self.assertEqual(len(errors), 2)
        # train/valid first, then valid/test
        self.assertIn("train_end", errors[0])
        self.assertIn("valid_end", errors[1])

    def test_custom_lookahead_horizon(self):
        cal = _make_calendar(date(2024, 1, 1), 100)
        # 4-day gap — passes for lookahead=2 but fails for lookahead=5
        win = dict(
            train_end=date(2024, 1, 10),
            valid_start=date(2024, 1, 15),
            valid_end=date(2024, 1, 25),
            test_start=date(2024, 1, 30),
        )
        self.assertEqual(
            validate_segment_embargo(**win, calendar=cal, lookahead_days=2),
            [],
        )
        errs = validate_segment_embargo(**win, calendar=cal, lookahead_days=5)
        self.assertEqual(len(errs), 2)

    def test_non_monotone_pair_skipped_not_double_flagged(self):
        """When ``valid_start <= train_end`` the date-ordering validator
        already flags it; the embargo validator must NOT also flag."""
        cal = _make_calendar(date(2024, 1, 1), 100)
        errors = validate_segment_embargo(
            train_end=date(2024, 1, 15),
            valid_start=date(2024, 1, 10),  # before train_end → skip
            valid_end=date(2024, 1, 20),
            test_start=date(2024, 1, 25),
            calendar=cal,
        )
        # Only valid/test pair is evaluated.
        self.assertEqual(len(errors), 0)


# ---------------------------------------------------------------------------
# Core integration — FeatureDatasetBuilder._validate enforces embargo
# ---------------------------------------------------------------------------


class CoreEmbargoIntegrationTests(unittest.TestCase):
    """The previously UI-only check now applies to every entry point
    that goes through ``FeatureDatasetBuilder.build``: main.py,
    walk-forward CLI, direct API calls, etc."""

    def _config(self, *, train_end="2024-01-10", valid_start="2024-01-15",
                valid_end="2024-01-25", test_start="2024-01-30",
                feature_handler="Alpha158"):
        return FeatureDatasetConfig(
            instruments="csi300",
            feature_handler=feature_handler,
            train_start="2023-01-01",
            train_end=train_end,
            valid_start=valid_start,
            valid_end=valid_end,
            test_start=test_start,
            test_end="2024-02-28",
        )

    def _patch_calendar(self, calendar_dates):
        """Return a context that stubs _load_trading_calendar with a
        synthetic calendar — avoids needing a real qlib bundle."""
        return patch.object(
            FeatureDatasetBuilder,
            "_load_trading_calendar",
            staticmethod(lambda *, start, end: calendar_dates),
        )

    def _patch_qlib_init(self):
        return patch(
            "src.data.feature_dataset_builder.is_canonical_qlib_initialized",
            return_value=True,
        )

    def test_validate_rejects_adjacent_alpha158_train_valid(self):
        cal = _make_calendar(date(2024, 1, 1), 60)
        config = self._config(train_end="2024-01-10", valid_start="2024-01-11")
        with self._patch_qlib_init(), self._patch_calendar(cal):
            with self.assertRaisesRegex(
                FeatureDatasetBuilderError, "label embargo"
            ):
                FeatureDatasetBuilder._validate(config)

    def test_validate_accepts_safe_alpha158_window(self):
        cal = _make_calendar(date(2024, 1, 1), 60)
        config = self._config()  # default has 4-day gaps
        with self._patch_qlib_init(), self._patch_calendar(cal):
            # Should NOT raise.
            FeatureDatasetBuilder._validate(config)

    def test_validate_skips_embargo_for_non_alpha158_handler(self):
        """MinedFactor has its own label semantics; the Alpha158
        embargo policy doesn't apply (the handler author is
        responsible for any equivalent check)."""
        cal = _make_calendar(date(2024, 1, 1), 60)
        config = self._config(
            train_end="2024-01-10", valid_start="2024-01-11",
            feature_handler="MinedFactor",
        )
        # Register a stub MinedFactor so the handler-existence check
        # in _validate doesn't bail before the embargo path.
        from src.data.feature_dataset_builder import register_feature_handler
        register_feature_handler("MinedFactor", lambda _cfg: None, replace=True)
        try:
            with self._patch_qlib_init(), self._patch_calendar(cal):
                # Should NOT raise — embargo skipped for non-Alpha158.
                FeatureDatasetBuilder._validate(config)
        finally:
            # Restore the registry for other tests.
            from src.data.feature_dataset_builder import (
                _reset_feature_handler_registry_to_defaults,
            )
            _reset_feature_handler_registry_to_defaults()

    def test_validate_skips_with_info_when_calendar_unreachable(self):
        """If qlib's calendar is unavailable (degraded provider, etc.),
        the check is skipped with an INFO log, not blocked. This
        matches the UI's behavior on empty-calendar metadata."""
        config = self._config(
            train_end="2024-01-10", valid_start="2024-01-11",
        )
        with self._patch_qlib_init(), self._patch_calendar(None), \
                self.assertLogs(
                    "src.data.feature_dataset_builder", level=logging.INFO
                ) as cap:
            # Should NOT raise even though the dates are adjacent.
            FeatureDatasetBuilder._validate(config)
        self.assertTrue(
            any("Skipping Alpha158 label embargo check" in m for m in cap.output),
            f"expected an INFO log about skipping the check; got {cap.output!r}",
        )


if __name__ == "__main__":
    unittest.main()
