"""Tests for PIT historical-ST reconstruction (src/data/st_history.py).

Covers the boundary decisions nailed in the C2-d PR2 Step-0 design:
start_date-inclusive as-of step function, end_date ignored, full-row dedup
(NOT key-subset), same-day "any ST -> ST", default non-ST before first record,
the compute_st_mask + apply seam, and fail-loud on bad/stale namechange data.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.core.microstructure_mask import apply_mask_to_predictions
from src.data.st_history import (
    StHistoryError,
    assert_covers,
    build_st_lookup,
    compute_st_mask,
    is_st_on,
    load_namechange,
    name_on,
)

_COLUMNS = ["ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"]


def _nc(rows: list[tuple]) -> pd.DataFrame:
    """Build a namechange frame from (ts_code, name, start[, end[, ann]]) tuples."""
    recs = []
    for r in rows:
        ts, name, start = r[0], r[1], r[2]
        end = r[3] if len(r) > 3 else None
        ann = r[4] if len(r) > 4 else start
        recs.append({
            "ts_code": ts, "name": name, "start_date": start,
            "end_date": end, "ann_date": ann, "change_reason": "",
        })
    return pd.DataFrame(recs, columns=_COLUMNS)


class AsOfReconstructionTests(unittest.TestCase):
    def test_start_date_boundary_is_inclusive(self) -> None:
        lk = build_st_lookup(_nc([("000001.SZ", "ST平安", "20200110")]))
        self.assertTrue(is_st_on(lk, "000001.SZ", "2020-01-10"))   # ON start -> ST
        self.assertFalse(is_st_on(lk, "000001.SZ", "2020-01-09"))  # day before -> none

    def test_asof_step_became_then_removed(self) -> None:
        lk = build_st_lookup(_nc([
            ("000001.SZ", "ST康美", "20200101"),
            ("000001.SZ", "康美药业", "20210601"),   # 摘帽 -> non-ST name
        ]))
        self.assertTrue(is_st_on(lk, "000001.SZ", "2020-06-01"))   # during ST
        self.assertTrue(is_st_on(lk, "000001.SZ", "2021-05-31"))   # day before removal
        self.assertFalse(is_st_on(lk, "000001.SZ", "2021-06-01"))  # removed
        self.assertEqual(name_on(lk, "000001.SZ", "2020-06-01"), "ST康美")

    def test_future_start_does_not_affect_earlier_date(self) -> None:
        lk = build_st_lookup(_nc([
            ("000002.SZ", "万科A", "20180101"),
            ("000002.SZ", "*ST万科", "20250101"),   # future relabel
        ]))
        self.assertFalse(is_st_on(lk, "000002.SZ", "2024-12-31"))  # no look-ahead
        self.assertTrue(is_st_on(lk, "000002.SZ", "2025-01-01"))

    def test_before_first_record_defaults_non_st(self) -> None:
        lk = build_st_lookup(_nc([("000003.SZ", "ST某", "20200101")]))
        self.assertFalse(is_st_on(lk, "000003.SZ", "2019-12-31"))
        self.assertIsNone(name_on(lk, "000003.SZ", "2019-12-31"))

    def test_absent_ts_defaults_non_st(self) -> None:
        lk = build_st_lookup(_nc([("000001.SZ", "ST平安", "20200101")]))
        self.assertFalse(is_st_on(lk, "999999.SZ", "2020-06-01"))
        self.assertIsNone(name_on(lk, "999999.SZ", "2020-06-01"))

    def test_end_date_is_ignored(self) -> None:
        # Misleading end_date in the distant past; as-of uses start_date only.
        lk = build_st_lookup(_nc([("000001.SZ", "ST平安", "20200101", "19990101")]))
        self.assertTrue(is_st_on(lk, "000001.SZ", "2020-06-01"))


class DedupAndAmbiguityTests(unittest.TestCase):
    def test_full_row_duplicates_collapse(self) -> None:
        lk = build_st_lookup(_nc([("000001.SZ", "ST平安", "20200101")] * 3))
        self.assertEqual(len(lk["000001.SZ"]), 1)
        self.assertTrue(is_st_on(lk, "000001.SZ", "2020-06-01"))

    def test_same_key_different_name_not_deduped_any_st(self) -> None:
        # Same (ts_code, start_date) with an ST and a non-ST name: a key-subset
        # dedup would drop one; full-row keeps both -> any-ST rule -> ST.
        lk = build_st_lookup(_nc([
            ("000995.SZ", "ST皇台", "20201216"),
            ("000995.SZ", "皇台酒业", "20201216"),
        ]))
        self.assertEqual(len(lk["000995.SZ"]), 1)  # one (ts,start) record
        self.assertTrue(is_st_on(lk, "000995.SZ", "2020-12-16"))  # any ST -> ST

    def test_same_day_all_non_st_stays_non_st(self) -> None:
        lk = build_st_lookup(_nc([
            ("000001.SZ", "平安A", "20200101"),
            ("000001.SZ", "平安B", "20200101"),
        ]))
        self.assertFalse(is_st_on(lk, "000001.SZ", "2020-06-01"))


class ComputeStMaskTests(unittest.TestCase):
    def test_mask_pairs_and_attribution(self) -> None:
        lk = build_st_lookup(_nc([
            ("000001.SZ", "ST平安", "20200101"),
            ("600000.SH", "浦发银行", "20180101"),
        ]))
        pairs = [("2020-06-01", "SZ000001"), ("2020-06-01", "SH600000")]
        mask, attr = compute_st_mask(pairs, lk)
        self.assertEqual(mask, frozenset({("2020-06-01", "SZ000001")}))
        self.assertEqual(
            attr,
            [{"date": "2020-06-01", "instrument": "SZ000001",
              "ts_code": "000001.SZ", "name": "ST平安"}],
        )

    def test_mask_seam_drops_st_rows_from_predictions(self) -> None:
        # compute_st_mask + apply_mask_to_predictions == the backtest seam.
        lk = build_st_lookup(_nc([("000001.SZ", "ST平安", "20200101")]))
        idx = pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2020-06-01"), "SZ000001"),
             (pd.Timestamp("2020-06-01"), "SH600000")],
            names=["datetime", "instrument"],
        )
        preds = pd.Series([0.9, 0.8], index=idx)
        pairs = [("2020-06-01", "SZ000001"), ("2020-06-01", "SH600000")]
        mask, _attr = compute_st_mask(pairs, lk)
        filtered, n_dropped = apply_mask_to_predictions(preds, mask)
        self.assertEqual(n_dropped, 1)
        self.assertEqual(
            list(filtered.index.get_level_values("instrument")), ["SH600000"],
        )


class FailLoudTests(unittest.TestCase):
    def test_load_missing_file_raises(self) -> None:
        with self.assertRaisesRegex(StHistoryError, "not found"):
            load_namechange("D:/no/such/namechange_xyz.parquet")

    def test_load_unreadable_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "nc.parquet"
            p.write_bytes(b"not a parquet")
            with self.assertRaisesRegex(StHistoryError, "could not be read"):
                load_namechange(p)

    def test_load_missing_column_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "nc.parquet"
            pd.DataFrame({"ts_code": ["000001.SZ"], "start_date": ["20200101"]}).to_parquet(p)
            with self.assertRaisesRegex(StHistoryError, "missing required column"):
                load_namechange(p)

    def test_load_empty_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "nc.parquet"
            pd.DataFrame(columns=_COLUMNS).to_parquet(p)
            with self.assertRaisesRegex(StHistoryError, "zero rows"):
                load_namechange(p)

    def test_build_missing_column_raises(self) -> None:
        with self.assertRaisesRegex(StHistoryError, "missing required column"):
            build_st_lookup(pd.DataFrame({"ts_code": ["x"], "name": ["y"]}))

    def test_assert_covers_stale_raises(self) -> None:
        nc = _nc([("000001.SZ", "ST平安", "20200101", None, "20200101")])
        with self.assertRaisesRegex(StHistoryError, "before the backtest end"):
            assert_covers(nc, "2025-12-31")

    def test_assert_covers_ok_when_snapshot_recent(self) -> None:
        nc = _nc([("000001.SZ", "ST平安", "20200101", None, "20260101")])
        assert_covers(nc, "2025-12-31")  # must not raise


if __name__ == "__main__":
    unittest.main()
