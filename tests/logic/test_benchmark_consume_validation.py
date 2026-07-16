"""PR-J — consume-time VALUE-level benchmark validation.

Two layers:
* ``validate_benchmark_values`` — pure invariants over synthetic pandas Series
  (no qlib): finite / positive / no intra-span gaps, and the TR>=price
  cumulative-return cross-check.
* ``BacktestRunner._validate_consumed_benchmark`` — the consume-path wiring,
  exercised with a mocked ``qlib.data.D`` so a defective benchmark fails loud
  BEFORE the backtest reads it.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.benchmark_data_contract import (  # noqa: E402
    validate_benchmark_values,
)
from src.core.backtest_runner import (  # noqa: E402
    BacktestRunner,
    BacktestRunnerError,
)

_DATES = ["2026-06-10", "2026-06-11", "2026-06-12", "2026-06-15", "2026-06-16"]


def _series(dates: list[str], vals: list[float]) -> pd.Series:
    return pd.Series(vals, index=pd.to_datetime(dates), name="$close", dtype="float64")


def _qlib_frame(series_by_code: dict[str, pd.Series]) -> pd.DataFrame:
    """A qlib ``D.features``-shaped frame (MultiIndex [instrument, datetime],
    one ``$close`` column) from {code: close-series}."""
    parts = []
    for code, s in series_by_code.items():
        idx = pd.MultiIndex.from_arrays(
            [[code] * len(s), s.index], names=["instrument", "datetime"]
        )
        parts.append(pd.DataFrame({"$close": s.to_numpy()}, index=idx))
    return pd.concat(parts) if parts else pd.DataFrame()


class ValidateBenchmarkValuesTests(unittest.TestCase):
    """Pure value-level invariants — no qlib."""

    def test_clean_series_ok(self) -> None:
        r = validate_benchmark_values(
            {"SH000300": _series(_DATES, [100, 101, 102, 101, 103])}
        )
        self.assertTrue(r.ok)
        self.assertEqual(r.errors, ())
        self.assertEqual(r.checked_codes, ("SH000300",))

    def test_nan_in_series_is_error(self) -> None:
        r = validate_benchmark_values(
            {"SH000300": _series(_DATES, [100, np.nan, 102, 101, 103])}
        )
        self.assertFalse(r.ok)
        self.assertTrue(any("non-finite" in e for e in r.errors))

    def test_nonpositive_close_is_error(self) -> None:
        r = validate_benchmark_values(
            {"SH000300": _series(_DATES, [100, 0.0, 102, -1.0, 103])}
        )
        self.assertFalse(r.ok)
        self.assertTrue(any("non-positive" in e for e in r.errors))

    def test_empty_series_is_error(self) -> None:
        r = validate_benchmark_values({"SH000300": _series([], [])})
        self.assertFalse(r.ok)
        self.assertTrue(any("empty" in e for e in r.errors))

    def test_tr_ge_price_cumret_ok(self) -> None:
        # TR cumulative return >= price cumulative return (dividends >= 0).
        price = _series(_DATES, [100, 101, 102, 101, 103])
        tr = _series(_DATES, [100, 101.5, 103.0, 102.5, 105.0])
        r = validate_benchmark_values(
            {"SH000300": price, "SH000300TR": tr},
            tr_price_pairs={"SH000300TR": "SH000300"},
        )
        self.assertTrue(r.ok, r.errors)

    def test_tr_below_price_cumret_is_warning_not_error(self) -> None:
        # price +15% cumret, TR only +3% → impossible (TR must reinvest
        # non-negative dividends) → stale/swapped TR. This is a WARNING, not a
        # run-aborting error: the live TR carries benign one-day stale prints
        # indistinguishable by magnitude from a short-window swap, and the TR is
        # not consumed for the price-benchmark excess-return. (deficit ~12% ≫
        # the 1e-2 default, so it surfaces.)
        price = _series(_DATES, [100, 105, 110, 108, 115])
        tr = _series(_DATES, [100, 101, 102, 101, 103])
        r = validate_benchmark_values(
            {"SH000300": price, "SH000300TR": tr},
            tr_price_pairs={"SH000300TR": "SH000300"},
        )
        self.assertTrue(r.ok)  # NOT a hard error
        self.assertEqual(r.errors, ())
        self.assertTrue(any("BELOW the price-index" in w for w in r.warnings))

    def test_transient_subpercent_tr_dip_does_not_warn(self) -> None:
        # A one-day stale print: TR flat for a day while price moves, recovering
        # next session — a ~0.4% transient deficit (below the 1e-2 default) must
        # NOT even warn (the P0 the self-review caught against the live bundle).
        price = _series(_DATES, [100.0, 100.4, 100.8, 101.2, 101.6])
        tr = _series(_DATES, [100.0, 100.0, 100.8, 101.2, 101.6])  # flat day 2
        r = validate_benchmark_values(
            {"SH000300": price, "SH000300TR": tr},
            tr_price_pairs={"SH000300TR": "SH000300"},
        )
        self.assertTrue(r.ok)
        self.assertEqual(
            [w for w in r.warnings if "BELOW the price-index" in w], []
        )

    def test_bad_non_consumed_sibling_is_not_a_hard_error(self) -> None:
        # codex P2: the consumed benchmark (SH000300) is clean; the TR sibling
        # has a NaN. With consumed_codes limited to the price index, the bad TR
        # must NOT hard-fail — only the consumed series is hard-checked. The
        # cross-check can't run on a non-finite sibling, so it warns.
        r = validate_benchmark_values(
            {
                "SH000300": _series(_DATES, [100, 101, 102, 101, 103]),
                "SH000300TR": _series(_DATES, [100, np.nan, 102, 101, 103]),
            },
            consumed_codes={"SH000300"},
            tr_price_pairs={"SH000300TR": "SH000300"},
        )
        self.assertTrue(r.ok)  # the NON-consumed sibling's NaN is not fatal
        self.assertEqual(r.errors, ())
        self.assertTrue(any("cross-check skipped" in w for w in r.warnings))

    def test_consumed_code_still_hard_checked(self) -> None:
        # The flip side: a NaN in the CONSUMED benchmark is still a hard error.
        r = validate_benchmark_values(
            {
                "SH000300": _series(_DATES, [100, np.nan, 102, 101, 103]),
                "SH000300TR": _series(_DATES, [100, 101, 102, 101, 103]),
            },
            consumed_codes={"SH000300"},
            tr_price_pairs={"SH000300TR": "SH000300"},
        )
        self.assertFalse(r.ok)
        self.assertTrue(any("non-finite" in e for e in r.errors))

    def test_sibling_nonpositive_after_base_warns_skipped(self) -> None:
        # codex P2 round 4: a non-consumed sibling with a zero/negative LATER in
        # the window (base is fine) must skip the cross-check (warn), not run the
        # cumret on invalid levels and look clean.
        price = _series(_DATES, [100, 101, 102, 101, 103])
        tr = _series(_DATES, [100, 101, 0.0, 101, 103])  # mid-window zero
        r = validate_benchmark_values(
            {"SH000300": price, "SH000300TR": tr},
            consumed_codes={"SH000300"},  # sibling NOT hard-checked
            tr_price_pairs={"SH000300TR": "SH000300"},
        )
        self.assertTrue(r.ok)  # non-consumed sibling defect is not a hard error
        self.assertTrue(any("cross-check skipped" in w for w in r.warnings))

    def test_insufficient_overlap_warns_skipped(self) -> None:
        # codex P2 round 3: both series present but sharing < 2 dates (here
        # disjoint) ⇒ the cumret check cannot run ⇒ a skipped warning, not a
        # silent no-op (and not an error).
        price = _series(["2026-06-10", "2026-06-11"], [100, 101])
        tr = _series(["2026-06-12", "2026-06-15"], [100, 101])
        r = validate_benchmark_values(
            {"SH000300": price, "SH000300TR": tr},
            consumed_codes={"SH000300"},
            tr_price_pairs={"SH000300TR": "SH000300"},
        )
        self.assertTrue(r.ok)
        self.assertTrue(any("cross-check skipped" in w for w in r.warnings))

    def test_pair_not_loaded_is_warning_not_error(self) -> None:
        r = validate_benchmark_values(
            {"SH000300": _series(_DATES, [100, 101, 102, 101, 103])},
            tr_price_pairs={"SH000300TR": "SH000300"},
        )
        self.assertTrue(r.ok)  # the single loaded series is fine
        self.assertTrue(any("cross-check skipped" in w for w in r.warnings))


class ConsumedBenchmarkWiringTests(unittest.TestCase):
    """``BacktestRunner._validate_consumed_benchmark`` with a mocked D."""

    def setUp(self) -> None:
        if importlib.util.find_spec("qlib") is None:
            self.skipTest("qlib not installed")

    @staticmethod
    def _patch_D(frame: pd.DataFrame):
        fake = mock.Mock()
        fake.features.return_value = frame
        return mock.patch("qlib.data.D", fake)

    def test_good_benchmark_with_tr_passes(self) -> None:
        frame = _qlib_frame({
            "SH000300": _series(_DATES, [100, 101, 102, 101, 103]),
            "SH000300TR": _series(_DATES, [100, 101.5, 103.0, 102.5, 105.0]),
        })
        with self._patch_D(frame):
            BacktestRunner._validate_consumed_benchmark(
                "SH000300", "2026-06-10", "2026-06-16"
            )  # no raise

    def test_non_canonical_benchmark_warns_through_consume_path(self) -> None:
        # PR-J wiring: the LOAD-time canonical-benchmark warning must fire from the
        # real _validate_consumed_benchmark call site (not just the helper) when the
        # consumed benchmark (here the SH000300 price control) is non-canonical.
        frame = _qlib_frame({
            "SH000300": _series(_DATES, [100, 101, 102, 101, 103]),
            "SH000300TR": _series(_DATES, [100, 101.5, 103.0, 102.5, 105.0]),
        })
        with self._patch_D(frame):
            with self.assertLogs("src.core.backtest_runner", level="WARNING") as cm:
                BacktestRunner._validate_consumed_benchmark(
                    "SH000300", "2026-06-10", "2026-06-16"
                )
        self.assertTrue(
            any("NOT one of the canonical" in line and "SH000300" in line
                for line in cm.output),
            f"expected the LOAD-time canonical-benchmark warning, got: {cm.output}",
        )

    def test_nan_benchmark_raises(self) -> None:
        frame = _qlib_frame(
            {"SH000300": _series(_DATES, [100, np.nan, 102, 101, 103])}
        )
        with self._patch_D(frame), self.assertRaisesRegex(
            BacktestRunnerError, "value-level validation FAILED"
        ):
            BacktestRunner._validate_consumed_benchmark(
                "SH000300", "2026-06-10", "2026-06-16"
            )

    def test_bad_tr_sibling_does_not_raise(self) -> None:
        # codex P2 at the wiring level: consumed SH000300 is clean, the TR
        # sibling has a NaN — the backtest (price benchmark) must NOT abort.
        frame = _qlib_frame({
            "SH000300": _series(_DATES, [100, 101, 102, 101, 103]),
            "SH000300TR": _series(_DATES, [100, np.nan, 102, 101, 103]),
        })
        with self._patch_D(frame):
            BacktestRunner._validate_consumed_benchmark(
                "SH000300", "2026-06-10", "2026-06-16"
            )  # no raise

    def test_absent_tr_sibling_warns_skipped_not_raises(self) -> None:
        # codex P2 round 2: when the bundle returns ONLY the consumed benchmark
        # (no TR sibling), the cross-check cannot run — it must still emit an
        # observable "cross-check skipped" warning (absent sibling != clean
        # cross-check), and must not raise.
        import logging

        frame = _qlib_frame({"SH000300": _series(_DATES, [100, 101, 102, 101, 103])})
        with self._patch_D(frame):
            with self.assertLogs("src.core.backtest_runner", level=logging.WARNING) as cm:
                BacktestRunner._validate_consumed_benchmark(
                    "SH000300", "2026-06-10", "2026-06-16"
                )  # no raise
        self.assertTrue(any("cross-check skipped" in line for line in cm.output))

    def test_fetch_pads_prewindow_for_ref_return(self) -> None:
        # codex P2 round 5: qlib's benchmark return is $close/Ref($close,1)-1,
        # so the prior-day close is consumed on the first eval day. The fetch
        # must start BEFORE the eval window so that pre-window close is validated.
        frame = _qlib_frame({"SH000300": _series(_DATES, [100, 101, 102, 101, 103])})
        fake = mock.Mock()
        fake.features.return_value = frame
        with mock.patch("qlib.data.D", fake):
            BacktestRunner._validate_consumed_benchmark(
                "SH000300", "2026-06-10", "2026-06-16"
            )
        kwargs = fake.features.call_args.kwargs
        self.assertLess(kwargs["start_time"], "2026-06-10")   # padded back
        self.assertEqual(kwargs["end_time"], "2026-06-16")

    def test_no_prewindow_row_warns(self) -> None:
        # codex P2 round 6: if the (padded) fetch returns NO row before `start`,
        # qlib's first benchmark return uses an unvalidated prior level — warn.
        import logging

        frame = _qlib_frame({"SH000300": _series(_DATES, [100, 101, 102, 101, 103])})
        fake = mock.Mock()
        fake.features.return_value = frame  # _DATES[0] == start, no prior row
        with mock.patch("qlib.data.D", fake):
            with self.assertLogs("src.core.backtest_runner", level=logging.WARNING) as cm:
                BacktestRunner._validate_consumed_benchmark(
                    "SH000300", "2026-06-10", "2026-06-16"
                )
        self.assertTrue(any("no pre-window close" in ln for ln in cm.output))

    def test_prewindow_row_present_no_prewindow_warning(self) -> None:
        import logging

        dates = ["2026-06-09", "2026-06-10", "2026-06-11", "2026-06-12", "2026-06-15"]
        frame = _qlib_frame({"SH000300": _series(dates, [99, 100, 101, 102, 101])})
        fake = mock.Mock()
        fake.features.return_value = frame  # has 2026-06-09 < start
        with mock.patch("qlib.data.D", fake):
            with self.assertLogs("src.core.backtest_runner", level=logging.WARNING) as cm:
                BacktestRunner._validate_consumed_benchmark(
                    "SH000300", "2026-06-10", "2026-06-16"
                )
        self.assertFalse(any("no pre-window close" in ln for ln in cm.output))

    def test_missing_benchmark_raises(self) -> None:
        with self._patch_D(pd.DataFrame()), self.assertRaisesRegex(
            BacktestRunnerError, "no rows"
        ):
            BacktestRunner._validate_consumed_benchmark(
                "SH000300", "2026-06-10", "2026-06-16"
            )

    def test_tr_corruption_warns_does_not_raise(self) -> None:
        # A sustained TR-below-price deficit is a WARNING (logged), not a
        # run-aborting error — the consumed price benchmark is fine, and a
        # transient one-day TR dip must never break a backtest, so the TR
        # cross-check never raises (only finite/positive/no-gaps do).
        import logging

        frame = _qlib_frame({
            "SH000300": _series(_DATES, [100, 105, 110, 108, 115]),
            "SH000300TR": _series(_DATES, [100, 101, 102, 101, 103]),
        })
        with self._patch_D(frame):
            with self.assertLogs("src.core.backtest_runner", level=logging.WARNING) as cm:
                BacktestRunner._validate_consumed_benchmark(
                    "SH000300", "2026-06-10", "2026-06-16"
                )  # no raise
        self.assertTrue(any("BELOW the price-index" in line for line in cm.output))


if __name__ == "__main__":
    unittest.main()
