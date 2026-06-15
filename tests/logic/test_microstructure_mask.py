"""Unit tests for ``src.core.microstructure_mask``.

Audit P0-3 / openspec/changes/add-microstructure-mask.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

from src.core.microstructure_mask import (  # noqa: E402
    MicrostructureMaskError,
    MicrostructureMaskResult,
    apply_mask_to_predictions,
    compute_unavailable_mask,
    ts_to_iso_date,
)


def _build_ohlcv(
    rows: list[tuple[str, str, float, float, float, float]],
) -> pd.DataFrame:
    """Build a qlib-style (instrument, datetime) MultiIndex frame.

    ``rows`` is a list of ``(instrument, date_iso, volume, high,
    low, close)`` tuples. Produced columns are ``$volume``,
    ``$high``, ``$low``, ``$close`` to match qlib's emit style.
    """
    df = pd.DataFrame(
        [
            {
                "instrument": inst,
                "datetime": pd.Timestamp(date_iso),
                "$volume": vol,
                "$high": hi,
                "$low": lo,
                "$close": cl,
            }
            for inst, date_iso, vol, hi, lo, cl in rows
        ]
    )
    df = df.set_index(["instrument", "datetime"]).sort_index()
    return df


class MaskResultShapeTests(unittest.TestCase):
    def test_empty_result_constructs(self) -> None:
        r = MicrostructureMaskResult(
            masked=frozenset(), n_suspended=0, n_one_price_days=0,
        )
        self.assertEqual(r.total_masked, 0)

    def test_rejects_negative_counts(self) -> None:
        with self.assertRaisesRegex(
            MicrostructureMaskError, "non-negative"
        ):
            MicrostructureMaskResult(
                masked=frozenset(), n_suspended=-1, n_one_price_days=0,
            )

    def test_frozen(self) -> None:
        r = MicrostructureMaskResult(
            masked=frozenset(), n_suspended=0, n_one_price_days=0,
        )
        # ``@dataclass(frozen=True)`` raises ``FrozenInstanceError``
        # (subclass of AttributeError) on field assignment.
        with self.assertRaises(AttributeError):
            r.n_suspended = 5  # type: ignore[misc]


class ComputeUnavailableMaskTests(unittest.TestCase):
    """All tests use ``patch.dict('sys.modules', {'qlib.data': ...})``
    to inject a fake ``D`` whose ``features(...)`` returns the
    OHLCV frame we built. No real qlib needed.
    """

    def _run_compute(
        self,
        ohlcv_df: pd.DataFrame,
        instruments: list[str] | None = None,
        start: str = "2024-01-02",
        end: str = "2024-01-10",
    ) -> MicrostructureMaskResult:
        fake_D = MagicMock()
        fake_D.features.return_value = ohlcv_df
        with patch.dict(
            "sys.modules",
            {"qlib.data": MagicMock(D=fake_D)},
        ):
            return compute_unavailable_mask(
                instruments=instruments
                or sorted(ohlcv_df.index.get_level_values("instrument").unique()),
                start_date=start, end_date=end,
            )

    def test_all_trading_universe_empty_mask(self) -> None:
        ohlcv = _build_ohlcv([
            ("SH600000", "2024-01-02", 1_000_000, 11.0, 10.5, 10.8),
            ("SH600000", "2024-01-03", 1_200_000, 11.2, 10.7, 11.0),
            ("SZ300001", "2024-01-02",   500_000,  8.5,  8.1,  8.3),
        ])
        result = self._run_compute(ohlcv)
        self.assertEqual(result.masked, frozenset())
        self.assertEqual(result.n_suspended, 0)
        self.assertEqual(result.n_one_price_days, 0)
        self.assertEqual(result.total_masked, 0)

    def test_suspended_day_volume_zero(self) -> None:
        ohlcv = _build_ohlcv([
            ("SH600000", "2024-01-02", 0, 10.0, 10.0, 10.0),  # suspended
            ("SH600000", "2024-01-03", 1_000_000, 11.0, 10.5, 10.8),
        ])
        result = self._run_compute(ohlcv)
        self.assertEqual(result.n_suspended, 1)
        self.assertEqual(result.n_one_price_days, 0)
        self.assertIn(("2024-01-02", "SH600000"), result.masked)
        self.assertNotIn(("2024-01-03", "SH600000"), result.masked)

    def test_suspended_day_close_nan(self) -> None:
        """qlib sometimes writes NaN close for non-trading bins
        even when volume is reported as 0 — both paths should
        mark the day as suspended."""
        ohlcv = _build_ohlcv([
            ("SH600000", "2024-01-02", float("nan"), float("nan"),
             float("nan"), float("nan")),
        ])
        result = self._run_compute(ohlcv)
        self.assertEqual(result.n_suspended, 1)
        self.assertEqual(result.n_one_price_days, 0)
        self.assertIn(("2024-01-02", "SH600000"), result.masked)

    def test_one_price_lock_day(self) -> None:
        """``$high == $low`` with positive volume — a one-price
        trading day, almost always a limit-lock on A-share."""
        ohlcv = _build_ohlcv([
            ("SH600000", "2024-01-02", 1_000_000, 11.0, 11.0, 11.0),  # one-price
            ("SH600000", "2024-01-03", 1_200_000, 11.2, 10.7, 11.0),
        ])
        result = self._run_compute(ohlcv)
        self.assertEqual(result.n_suspended, 0)
        self.assertEqual(result.n_one_price_days, 1)
        self.assertIn(("2024-01-02", "SH600000"), result.masked)

    def test_suspended_and_one_price_in_same_window(self) -> None:
        """Same instrument, different days — one suspension + one
        one-price. Both end up in the mask."""
        ohlcv = _build_ohlcv([
            ("SH600000", "2024-01-02", 0, 10.0, 10.0, 10.0),  # suspended
            ("SH600000", "2024-01-03", 1_200_000, 11.0, 11.0, 11.0),  # one-price
            ("SH600000", "2024-01-04", 1_500_000, 11.5, 10.9, 11.2),  # normal
        ])
        result = self._run_compute(ohlcv)
        self.assertEqual(result.n_suspended, 1)
        self.assertEqual(result.n_one_price_days, 1)
        self.assertEqual(result.total_masked, 2)
        self.assertIn(("2024-01-02", "SH600000"), result.masked)
        self.assertIn(("2024-01-03", "SH600000"), result.masked)
        self.assertNotIn(("2024-01-04", "SH600000"), result.masked)

    def test_suspension_takes_precedence_over_one_price(self) -> None:
        """A day with volume=0 AND high==low (e.g. NaN==NaN-style
        edge cases shouldn't happen with our checks, but a bundle
        could plausibly report volume=0 with stale high==low close
        carried forward) is counted as suspended once, not as
        both regimes — the two are mutually exclusive."""
        ohlcv = _build_ohlcv([
            ("SH600000", "2024-01-02", 0, 10.0, 10.0, 10.0),
        ])
        result = self._run_compute(ohlcv)
        self.assertEqual(result.n_suspended, 1)
        self.assertEqual(result.n_one_price_days, 0)
        self.assertEqual(result.total_masked, 1)

    def test_multi_instrument_mixed(self) -> None:
        ohlcv = _build_ohlcv([
            ("SH600000", "2024-01-02", 1_000_000, 11.0, 10.5, 10.8),  # normal
            ("SH600000", "2024-01-03", 0,         0.0,  0.0,  0.0),   # suspended
            ("SZ300001", "2024-01-02", 500_000,  8.5,  8.5,  8.5),    # one-price
            ("SZ300001", "2024-01-03", 600_000,  8.7,  8.2,  8.5),    # normal
            ("BJ430047", "2024-01-02", 50_000,   3.2,  3.0,  3.1),    # normal
        ])
        result = self._run_compute(ohlcv)
        self.assertEqual(result.n_suspended, 1)
        self.assertEqual(result.n_one_price_days, 1)
        self.assertEqual(result.total_masked, 2)
        self.assertIn(("2024-01-03", "SH600000"), result.masked)
        self.assertIn(("2024-01-02", "SZ300001"), result.masked)

    def test_empty_instruments_returns_empty_mask(self) -> None:
        # When instruments is explicitly [], the helper short-
        # circuits before touching qlib — no OHLCV fetch needed.
        result = compute_unavailable_mask(
            instruments=[],
            start_date="2024-01-02",
            end_date="2024-01-10",
        )
        self.assertEqual(result.total_masked, 0)

    def test_rejects_malformed_start_date(self) -> None:
        with self.assertRaisesRegex(
            MicrostructureMaskError, "ISO YYYY-MM-DD"
        ):
            compute_unavailable_mask(
                instruments=["SH600000"],
                start_date="not-a-date",
                end_date="2024-01-31",
            )

    def test_rejects_end_before_start(self) -> None:
        with self.assertRaisesRegex(
            MicrostructureMaskError, "precedes start_date"
        ):
            compute_unavailable_mask(
                instruments=["SH600000"],
                start_date="2024-12-31",
                end_date="2024-01-01",
            )

    def test_qlib_fetch_failure_wrapped(self) -> None:
        """When ``D.features`` raises, the helper re-raises as
        ``MicrostructureMaskError`` so callers in the canonical
        path can catch the boundary type."""
        fake_D = MagicMock()
        fake_D.features.side_effect = RuntimeError("simulated qlib failure")
        with patch.dict(
            "sys.modules",
            {"qlib.data": MagicMock(D=fake_D)},
        ):
            with self.assertRaisesRegex(
                MicrostructureMaskError, "OHLCV fetch failed"
            ):
                compute_unavailable_mask(
                    instruments=["SH600000"],
                    start_date="2024-01-02",
                    end_date="2024-01-10",
                )

    def test_routes_through_pit_provider_when_supplied(self) -> None:
        """When ``pit_provider`` is set, the helper calls
        ``pit_provider.get_features`` instead of ``D.features``.
        Audit P0-6 compliance pattern."""
        ohlcv = _build_ohlcv([
            ("SH600000", "2024-01-02", 1_000_000, 11.0, 10.5, 10.8),
        ])
        fake_pit = MagicMock()
        fake_pit.get_features.return_value = ohlcv
        # qlib.data MUST NOT be consulted when pit_provider is set.
        fake_D = MagicMock()
        fake_D.features.side_effect = AssertionError(
            "D.features was called despite pit_provider being supplied",
        )
        with patch.dict(
            "sys.modules",
            {"qlib.data": MagicMock(D=fake_D)},
        ):
            result = compute_unavailable_mask(
                instruments=["SH600000"],
                start_date="2024-01-02",
                end_date="2024-01-10",
                pit_provider=fake_pit,
            )
        self.assertEqual(result.total_masked, 0)
        fake_pit.get_features.assert_called_once()

    def test_missing_required_columns_raises(self) -> None:
        """If the qlib bundle is missing $high/$low, the helper
        raises ``MicrostructureMaskError`` so the canonical path
        fails loudly rather than silently producing an empty
        mask. Audit P0-3."""
        # Build a frame missing $high and $low.
        df = pd.DataFrame(
            [{
                "instrument": "SH600000",
                "datetime": pd.Timestamp("2024-01-02"),
                "$volume": 1_000_000,
                "$close": 10.0,
            }]
        ).set_index(["instrument", "datetime"])
        with self.assertRaisesRegex(
            MicrostructureMaskError, "missing required columns"
        ):
            self._run_compute(df)


class ApplyMaskToPredictionsTests(unittest.TestCase):
    def _build_predictions(self) -> pd.Series:
        idx = pd.MultiIndex.from_tuples(
            [
                (pd.Timestamp("2024-01-02"), "SH600000"),
                (pd.Timestamp("2024-01-02"), "SZ300001"),
                (pd.Timestamp("2024-01-03"), "SH600000"),
                (pd.Timestamp("2024-01-03"), "SZ300001"),
            ],
            names=["datetime", "instrument"],
        )
        return pd.Series([0.5, 0.4, 0.3, 0.2], index=idx)

    def test_empty_mask_returns_same_series(self) -> None:
        preds = self._build_predictions()
        out, n = apply_mask_to_predictions(preds, frozenset())
        self.assertIs(out, preds)
        self.assertEqual(n, 0)

    def test_empty_mask_via_result_object(self) -> None:
        preds = self._build_predictions()
        result = MicrostructureMaskResult(
            masked=frozenset(), n_suspended=0, n_one_price_days=0,
        )
        out, n = apply_mask_to_predictions(preds, result)
        self.assertIs(out, preds)
        self.assertEqual(n, 0)

    def test_drops_matching_rows(self) -> None:
        preds = self._build_predictions()
        mask = frozenset({
            ("2024-01-02", "SH600000"),
            ("2024-01-03", "SZ300001"),
        })
        out, n = apply_mask_to_predictions(preds, mask)
        self.assertEqual(n, 2)
        self.assertEqual(len(out), 2)
        self.assertIn(
            (pd.Timestamp("2024-01-02"), "SZ300001"), list(out.index),
        )
        self.assertIn(
            (pd.Timestamp("2024-01-03"), "SH600000"), list(out.index),
        )

    def test_mask_with_no_intersection_is_noop(self) -> None:
        """Mask is non-empty but none of its (date, instrument)
        pairs hit predictions — e.g. mask is for a different
        universe slice. n_dropped == 0 and predictions returned
        unchanged."""
        preds = self._build_predictions()
        mask = frozenset({("2024-01-02", "SH999999")})
        out, n = apply_mask_to_predictions(preds, mask)
        self.assertEqual(n, 0)
        self.assertIs(out, preds)

    def test_rejects_non_series_input(self) -> None:
        mask = frozenset({("2024-01-02", "SH600000")})
        with self.assertRaisesRegex(
            MicrostructureMaskError, "must be a pd.Series"
        ):
            apply_mask_to_predictions([0.5, 0.4], mask)  # type: ignore[arg-type]

    def test_rejects_non_multiindex(self) -> None:
        preds = pd.Series([1.0, 2.0], index=["a", "b"])
        mask = frozenset({("2024-01-02", "SH600000")})
        with self.assertRaisesRegex(
            MicrostructureMaskError, "MultiIndex"
        ):
            apply_mask_to_predictions(preds, mask)


class TsToIsoDateTests(unittest.TestCase):
    """The shared parity helper must yield the SAME YYYY-MM-DD string for every
    qlib datetime-level shape, so mask keys and prediction-index dates match."""

    def test_pandas_timestamp(self) -> None:
        self.assertEqual(
            ts_to_iso_date(pd.Timestamp("2026-06-10 09:30:00")), "2026-06-10",
        )

    def test_python_datetime(self) -> None:
        from datetime import datetime
        self.assertEqual(ts_to_iso_date(datetime(2026, 6, 10, 15, 0)), "2026-06-10")

    def test_python_date(self) -> None:
        from datetime import date as _date
        # date has no .date() method -> str()[:10] fallback, still YYYY-MM-DD.
        self.assertEqual(ts_to_iso_date(_date(2026, 6, 10)), "2026-06-10")

    def test_numpy_datetime64(self) -> None:
        import numpy as np
        self.assertEqual(
            ts_to_iso_date(np.datetime64("2026-06-10T09:30")), "2026-06-10",
        )

    def test_all_shapes_agree(self) -> None:
        from datetime import datetime

        import numpy as np
        outs = {
            ts_to_iso_date(pd.Timestamp("2026-06-10")),
            ts_to_iso_date(datetime(2026, 6, 10)),
            ts_to_iso_date(np.datetime64("2026-06-10")),
        }
        self.assertEqual(outs, {"2026-06-10"})  # parity across shapes


if __name__ == "__main__":
    unittest.main()
