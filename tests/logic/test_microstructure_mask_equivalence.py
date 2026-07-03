"""Equivalence tests for the audit-P1 microstructure-mask vectorization.

The OLD per-row loops, copied VERBATIM from the pre-vectorization
implementation (git ``9c27099``), serve as the reference. The new vectorized
implementation must produce EXACTLY the same mask set, counts, filtered
values and drop counts on every edge case — the mask feeds the OFFICIAL
backtest input, so any output difference is a correctness bug, not a perf
trade-off (CI's REGEN-2 replay is the final bit-identity judge).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import numpy as np
import pandas as pd

from src.core.microstructure_mask import (  # noqa: E402
    apply_mask_to_predictions,
    compute_unavailable_mask,
    ts_to_iso_date,
)


def _old_loop_mask(df: pd.DataFrame) -> tuple[frozenset, int, int]:
    """The pre-vectorization compute loop, verbatim (clean-column frame in)."""
    inst_level = df.index.get_level_values("instrument")
    date_level = df.index.get_level_values("datetime")
    volume = df["volume"]
    close = df["close"]
    high = df["high"]
    low = df["low"]
    suspended_mask = (volume.isna()) | (volume < 1) | (close.isna())
    one_price_mask = (
        (~suspended_mask) & (high.notna()) & (low.notna()) & (high == low)
    )
    masked_pairs: list[tuple[str, str]] = []
    n_suspended = 0
    n_one_price = 0
    sus_values = suspended_mask.to_numpy(copy=False)
    one_values = one_price_mask.to_numpy(copy=False)
    for i in range(len(df)):
        if sus_values[i]:
            ts = date_level[i]
            date_iso = ts_to_iso_date(ts)
            masked_pairs.append((date_iso, str(inst_level[i])))
            n_suspended += 1
        elif one_values[i]:
            ts = date_level[i]
            date_iso = ts_to_iso_date(ts)
            masked_pairs.append((date_iso, str(inst_level[i])))
            n_one_price += 1
    return frozenset(masked_pairs), n_suspended, n_one_price


def _old_loop_apply(
    predictions: pd.Series, pair_set: frozenset,
) -> tuple[pd.Series, int]:
    """The pre-vectorization predictions filter loop, verbatim."""
    if not pair_set:
        return predictions, 0
    date_level = predictions.index.get_level_values("datetime")
    inst_level = predictions.index.get_level_values("instrument")
    keep = []
    n_dropped = 0
    for i in range(len(predictions)):
        ts = date_level[i]
        date_iso = ts_to_iso_date(ts)
        if (date_iso, str(inst_level[i])) in pair_set:
            keep.append(False)
            n_dropped += 1
        else:
            keep.append(True)
    if n_dropped == 0:
        return predictions, 0
    return predictions[keep], n_dropped


def _dollar_frame(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "instrument": i, "datetime": d, "$volume": v,
                "$high": h, "$low": lo, "$close": c,
            }
            for i, d, v, h, lo, c in rows
        ]
    ).set_index(["instrument", "datetime"]).sort_index()


class VectorizationEquivalenceTests(unittest.TestCase):
    """New vectorized implementation == old loop, bit for bit (audit P1)."""

    def _assert_compute_equivalent(self, ohlcv: pd.DataFrame) -> None:
        fake_D = MagicMock()
        fake_D.features.return_value = ohlcv
        with patch.dict("sys.modules", {"qlib.data": MagicMock(D=fake_D)}):
            insts = sorted(
                str(x)
                for x in ohlcv.index.get_level_values("instrument").unique()
            )
            new = compute_unavailable_mask(
                instruments=insts,
                start_date="2024-01-02", end_date="2024-12-31",
            )
        clean = ohlcv.rename(columns={
            "$volume": "volume", "$high": "high",
            "$low": "low", "$close": "close",
        })
        old_set, old_sus, old_one = _old_loop_mask(clean)
        self.assertEqual(new.masked, old_set)
        self.assertEqual(new.n_suspended, old_sus)
        self.assertEqual(new.n_one_price_days, old_one)

    def test_randomized_panel_equivalent(self) -> None:
        rng = np.random.default_rng(42)
        rows = []
        dates = pd.date_range("2024-01-02", periods=30, freq="B")
        for k in range(40):
            inst = f"SH60{k:04d}"
            for d in dates:
                roll = rng.random()
                if roll < 0.10:     # suspended: zero volume
                    rows.append((inst, d, 0.0, 10.0, 10.0, 10.0))
                elif roll < 0.15:   # suspended: NaN volume
                    rows.append((inst, d, float("nan"), 10.0, 9.5, 9.8))
                elif roll < 0.20:   # suspended: NaN close
                    rows.append((inst, d, 1e6, 10.0, 9.5, float("nan")))
                elif roll < 0.30:   # one-price lock
                    rows.append((inst, d, 1e6, 10.0, 10.0, 10.0))
                else:               # normal trading day
                    rows.append((inst, d, 1e6, 10.0 + rng.random(), 9.5, 9.9))
        self._assert_compute_equivalent(_dollar_frame(rows))

    def test_edge_panels_equivalent(self) -> None:
        t = pd.Timestamp
        cases: dict[str, list[tuple]] = {
            "all_masked": [
                ("SH600000", t("2024-01-02"), 0.0, 10.0, 10.0, 10.0),
                ("SH600001", t("2024-01-02"), 1e6, 10.0, 10.0, 10.0),
            ],
            "close_all_nan": [
                ("SH600000", t("2024-01-02"), 1e6, 10.0, 9.5, float("nan")),
                ("SH600001", t("2024-01-03"), 1e6, 11.0, 10.5, float("nan")),
            ],
            "single_row": [
                ("SH600000", t("2024-01-02"), 0.0, 10.0, 10.0, 10.0),
            ],
            "duplicate_pair_rows": [
                ("SH600000", t("2024-01-02"), 0.0, 10.0, 10.0, 10.0),
                ("SH600000", t("2024-01-02"), 0.0, 10.0, 10.0, 10.0),
                ("SH600000", t("2024-01-02"), 1e6, 10.0, 10.0, 10.0),
            ],
            "high_low_nan_not_locked": [
                ("SH600000", t("2024-01-02"), 1e6, float("nan"), 9.5, 9.9),
                ("SH600001", t("2024-01-02"), 1e6, 10.0, float("nan"), 9.9),
            ],
        }
        for name, rows in cases.items():
            with self.subTest(case=name):
                self._assert_compute_equivalent(_dollar_frame(rows))

    def test_tz_aware_panel_equivalent(self) -> None:
        t = pd.Timestamp
        rows = [
            ("SH600000", t("2024-01-02", tz="Asia/Shanghai"), 0.0, 10.0, 10.0, 10.0),
            ("SH600000", t("2024-01-03", tz="Asia/Shanghai"), 1e6, 10.0, 10.0, 10.0),
            ("SH600001", t("2024-01-02", tz="Asia/Shanghai"), 1e6, 11.0, 10.5, 10.8),
        ]
        self._assert_compute_equivalent(_dollar_frame(rows))

    def test_mixed_tz_grouping_trap_fixed_at_helper_level(self) -> None:
        # codex P2 on #319: two Timestamps at the SAME INSTANT compare equal
        # (factorize would group them) but have DIFFERENT local dates — UTC
        # 16:00 == Asia/Shanghai next-day 00:00. NOTE pandas NORMALIZES
        # mixed-tz values to a single UTC datetime64 level at Index/MultiIndex
        # CONSTRUCTION (verified below), so this state cannot arrive through
        # the public API — the fix is defence-in-depth at the helper, which
        # accepts any level-like. A raw numpy object array CAN hold the state:
        # the helper must fall back to row-wise conversion and yield TWO
        # distinct ISO dates, exactly like the old per-row loop.
        from src.core.microstructure_mask import _iso_dates_for

        utc_ts = pd.Timestamp("2024-01-02 16:00", tz="UTC")
        sha_ts = pd.Timestamp("2024-01-03 00:00", tz="Asia/Shanghai")
        assert utc_ts == sha_ts                    # same instant: the trap
        assert utc_ts.date() != sha_ts.date()      # different local dates
        raw = np.array([utc_ts, sha_ts], dtype=object)
        out = list(_iso_dates_for(raw))
        old = [ts_to_iso_date(t) for t in raw]     # the old loop, per row
        self.assertEqual(out, old)
        self.assertEqual(out, ["2024-01-02", "2024-01-03"])
        # and the unreachability claim itself, pinned: pandas normalizes
        lvl = pd.MultiIndex.from_arrays(
            [pd.Index([utc_ts, sha_ts], dtype=object),
             pd.Index(["A", "B"], dtype=object)],
            names=["datetime", "instrument"],
        ).get_level_values("datetime")
        self.assertTrue(str(lvl.dtype).startswith("datetime64"))

    def test_str_projection_trap_fixed_at_helper_level(self) -> None:
        # the str-side twin: 1 == 1.0 (factorize would group) but
        # str(1) != str(1.0). The helper's infer_dtype guard must route a
        # non-pure-string level through the row-wise fallback.
        from src.core.microstructure_mask import _str_insts_for

        raw = np.array([1, 1.0, "SH600000"], dtype=object)
        out = list(_str_insts_for(raw))
        self.assertEqual(out, ["1", "1.0", "SH600000"])

    def test_object_datetime_level_equivalent_end_to_end(self) -> None:
        # a CONSTRUCTIBLE object datetime level (mixing a Timestamp with an
        # ISO string resists pandas' datetime coercion) goes through the real
        # API and must match the old loop verbatim (row-wise fallback path).
        idx = pd.MultiIndex.from_arrays(
            [pd.Index(["SH600000", "SH600001"], dtype=object),
             pd.Index([pd.Timestamp("2024-01-02"), "2024-01-03 00:00:00"],
                      dtype=object)],
            names=["instrument", "datetime"],
        )
        df = pd.DataFrame(
            {"$volume": [0.0, 0.0], "$high": [10.0, 10.0],
             "$low": [10.0, 10.0], "$close": [10.0, 10.0]},
            index=idx,
        )
        if not str(idx.get_level_values("datetime").dtype).startswith("object"):
            self.skipTest("pandas coerced the mixed level; fallback unreachable here")
        self._assert_compute_equivalent(df)
        clean = df.rename(columns={
            "$volume": "volume", "$high": "high",
            "$low": "low", "$close": "close",
        })
        old_set, _, _ = _old_loop_mask(clean)
        self.assertEqual({d for d, _ in old_set}, {"2024-01-02", "2024-01-03"})

    # -- predictions filter --------------------------------------------------

    def _preds(self, tz: str | None = None) -> pd.Series:
        dates = pd.date_range("2024-01-02", periods=5, freq="B", tz=tz)
        idx = pd.MultiIndex.from_product(
            [dates, ["SH600000", "SH600001", "SZ300001"]],
            names=["datetime", "instrument"],
        )
        return pd.Series(range(len(idx)), index=idx, dtype=float, name="score")

    def _assert_apply_equivalent(
        self, preds: pd.Series, pair_set: frozenset,
    ) -> None:
        new_f, new_n = apply_mask_to_predictions(preds, pair_set)
        old_f, old_n = _old_loop_apply(preds, pair_set)
        self.assertEqual(new_n, old_n)
        pd.testing.assert_series_equal(new_f, old_f)

    def test_apply_equivalent_hits_order_and_values(self) -> None:
        preds = self._preds()
        pair_set = frozenset({
            ("2024-01-02", "SH600000"),
            ("2024-01-04", "SZ300001"),
            ("2024-01-05", "SH600001"),
        })
        self._assert_apply_equivalent(preds, pair_set)

    def test_apply_equivalent_all_dropped(self) -> None:
        preds = self._preds()
        pair_set = frozenset(
            (d.date().isoformat(), i) for d, i in preds.index
        )
        self._assert_apply_equivalent(preds, pair_set)

    def test_apply_equivalent_tz_aware(self) -> None:
        preds = self._preds(tz="Asia/Shanghai")
        pair_set = frozenset({("2024-01-03", "SH600001")})
        self._assert_apply_equivalent(preds, pair_set)

    def test_apply_equivalent_object_datetime_level(self) -> None:
        # the apply-side fallback: a constructible OBJECT datetime level
        # (Timestamp + ISO string) matched against the mask by each row's own
        # ts_to_iso_date projection, exactly as the old row loop did.
        idx = pd.MultiIndex.from_arrays(
            [pd.Index([pd.Timestamp("2024-01-02"), "2024-01-03 00:00:00"],
                      dtype=object),
             pd.Index(["SH600000", "SH600001"], dtype=object)],
            names=["datetime", "instrument"],
        )
        if not str(idx.get_level_values("datetime").dtype).startswith("object"):
            self.skipTest("pandas coerced the mixed level; fallback unreachable here")
        preds = pd.Series([0.1, 0.2], index=idx, name="score")
        pair_set = frozenset({("2024-01-03", "SH600001")})
        self._assert_apply_equivalent(preds, pair_set)
        new_f, new_n = apply_mask_to_predictions(preds, pair_set)
        self.assertEqual(new_n, 1)
        self.assertEqual(list(new_f.index.get_level_values("instrument")), ["SH600000"])

    def test_apply_no_hit_returns_same_object(self) -> None:
        # the non-empty-mask / zero-hit fast path must keep IDENTITY (no copy)
        preds = self._preds()
        pair_set = frozenset({("1999-01-01", "SH999999")})
        new_f, new_n = apply_mask_to_predictions(preds, pair_set)
        self.assertEqual(new_n, 0)
        self.assertIs(new_f, preds)

    def test_apply_empty_mask_returns_same_object(self) -> None:
        preds = self._preds()
        new_f, new_n = apply_mask_to_predictions(preds, frozenset())
        self.assertEqual(new_n, 0)
        self.assertIs(new_f, preds)


if __name__ == "__main__":
    unittest.main()
