"""Tests for ``src.data.pit.index_membership.IndexMembershipResolver``."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.pit.index_membership import (  # noqa: E402
    MEMBERSHIP_DATE_TOLERANCE_DAYS,
    QLIB_OPEN_END_DATE,
    IndexMembershipError,
    IndexMembershipResolver,
    _to_iso_date,
    _to_qlib_ticker,
)


def _write_index_weight_parquet(
    path: Path,
    index_code: str,
    snapshots: list[tuple[str, list[str]]],
) -> None:
    """``snapshots`` is a list of ``(YYYYMMDD, [Tushare-style ts_codes])``."""
    rows = []
    for trade_date, tickers in snapshots:
        for con_code in tickers:
            rows.append({
                "index_code": index_code,
                "con_code": con_code,
                "trade_date": trade_date,
                "weight": 1.0,
            })
    df = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _write_refs(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


class HelperTests(unittest.TestCase):

    def test_to_iso_date(self) -> None:
        self.assertEqual(_to_iso_date("20220630"), "2022-06-30")

    def test_to_iso_date_rejects_short(self) -> None:
        with self.assertRaises(ValueError):
            _to_iso_date("2022")

    def test_to_qlib_ticker(self) -> None:
        self.assertEqual(_to_qlib_ticker("002594.SZ"), "SZ002594")
        self.assertEqual(_to_qlib_ticker("600519.SH"), "SH600519")


class RunBuildingTests(unittest.TestCase):
    """Run-detection algorithm at the heart of the resolver."""

    def _resolve(
        self, snapshots: list[tuple[str, list[str]]], with_refs: dict | None = None,
    ):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tmp_path = Path(tmp.name)
        _write_index_weight_parquet(
            tmp_path / "index_weight" / "000300.SH.parquet",
            "000300.SH", snapshots,
        )
        refs_path = None
        if with_refs is not None:
            refs_path = tmp_path / "refs.yaml"
            _write_refs(refs_path, with_refs)

        resolver = IndexMembershipResolver(
            tushare_dir=tmp_path,
            output_dir=tmp_path / "out",
            reference_cases_path=refs_path,
            indices=("000300.SH",),
        )
        results = resolver.resolve()
        out = (tmp_path / "out" / "instruments" / "csi300.txt").read_text(
            encoding="utf-8"
        )
        return results, out

    def test_continuous_membership_open_ended(self) -> None:
        """Ticker present in every snapshot -> single run ending at 2099-12-31."""
        _, out = self._resolve([
            ("20200131", ["600519.SH"]),
            ("20200229", ["600519.SH"]),
            ("20200331", ["600519.SH"]),
        ])
        self.assertEqual(
            out.strip(),
            "SH600519\t2020-01-31\t2099-12-31",
        )

    def test_leave_closes_run_at_last_present_snapshot(self) -> None:
        # 600519 acts as an "anchor" — Tushare always returns 300 constituents
        # per CSI300 snapshot, so a snapshot date never has zero rows in
        # production. Test fixtures must mirror this to exercise the
        # absence-detection path.
        _, out = self._resolve([
            ("20220601", ["600015.SH", "600519.SH"]),
            ("20220630", ["600015.SH", "600519.SH"]),
            ("20220729", ["600519.SH"]),  # 600015 absent
            ("20220831", ["600519.SH"]),
        ])
        lines = sorted(out.strip().split("\n"))
        self.assertEqual(lines, [
            "SH600015\t2022-06-01\t2022-06-30",
            "SH600519\t2022-06-01\t2099-12-31",
        ])

    def test_enter_after_initial_snapshot(self) -> None:
        _, out = self._resolve([
            ("20191130", ["600519.SH"]),
            ("20191231", ["600519.SH", "002594.SZ"]),  # BYD enters
            ("20200131", ["600519.SH", "002594.SZ"]),
        ])
        lines = sorted(out.strip().split("\n"))
        self.assertEqual(lines, [
            "SH600519\t2019-11-30\t2099-12-31",
            "SZ002594\t2019-12-31\t2099-12-31",
        ])

    def test_re_entry_produces_two_runs(self) -> None:
        """A ticker that leaves and re-enters gets two separate runs."""
        _, out = self._resolve([
            ("20200131", ["600000.SH", "600519.SH"]),
            ("20200229", ["600000.SH", "600519.SH"]),
            ("20200331", ["600519.SH"]),                # 600000 leaves
            ("20200430", ["600519.SH"]),
            ("20200529", ["600000.SH", "600519.SH"]),   # 600000 re-enters
            ("20200630", ["600000.SH", "600519.SH"]),
        ])
        lines = sorted(out.strip().split("\n"))
        self.assertEqual(lines, [
            "SH600000\t2020-01-31\t2020-02-29",
            "SH600000\t2020-05-29\t2099-12-31",
            "SH600519\t2020-01-31\t2099-12-31",
        ])

    def test_result_summary_shape(self) -> None:
        results, _ = self._resolve([
            ("20200131", ["600519.SH", "600000.SH"]),
            ("20200229", ["600519.SH"]),
        ])
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.index_code, "000300.SH")
        self.assertEqual(r.distinct_tickers, 2)
        self.assertEqual(r.earliest_snapshot, "2020-01-31")
        self.assertEqual(r.latest_snapshot, "2020-02-29")
        # 600519 active (run to 2099), 600000 closed -> 2 runs
        self.assertEqual(r.run_count, 2)


class ReferenceValidationTests(unittest.TestCase):

    def _setup(self, snapshots, refs):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tmp_path = Path(tmp.name)
        _write_index_weight_parquet(
            tmp_path / "index_weight" / "000300.SH.parquet",
            "000300.SH", snapshots,
        )
        refs_path = tmp_path / "refs.yaml"
        _write_refs(refs_path, refs)
        return IndexMembershipResolver(
            tushare_dir=tmp_path,
            output_dir=tmp_path / "out",
            reference_cases_path=refs_path,
            indices=("000300.SH",),
        )

    def test_enter_within_tolerance_passes(self) -> None:
        resolver = self._setup(
            [
                ("20191130", []),                  # 茅台 absent
                ("20191231", ["002594.SZ"]),       # BYD first appears here
                ("20200131", ["002594.SZ"]),
            ],
            {"index_membership_cases": {"csi300": [
                {"ticker": "SZ002594", "action": "enter",
                 "date": "2019-12-13"},  # 18 days before run_start 2019-12-31
            ]}},
        )
        results = resolver.resolve()
        self.assertEqual(results[0].reference_rows_matched, 1)

    def test_leave_within_tolerance_passes(self) -> None:
        """The known SH600015 华夏银行 caveat: monthly granularity emits
        run_end = 2022-06-30; asserted leave date 2022-06-13 is 17 days
        before, within the 35-day tolerance.
        """
        resolver = self._setup(
            [
                ("20220601", ["600015.SH", "600519.SH"]),
                ("20220630", ["600015.SH", "600519.SH"]),  # last present snapshot
                ("20220729", ["600519.SH"]),
            ],
            {"index_membership_cases": {"csi300": [
                {"ticker": "SH600015", "action": "leave",
                 "date": "2022-06-13"},
            ]}},
        )
        results = resolver.resolve()
        self.assertEqual(results[0].reference_rows_matched, 1)

    def test_enter_beyond_tolerance_raises(self) -> None:
        resolver = self._setup(
            [
                ("20200131", ["002594.SZ"]),
            ],
            {"index_membership_cases": {"csi300": [
                # Asserted enter is far before the first snapshot
                {"ticker": "SZ002594", "action": "enter",
                 "date": "2019-01-01"},  # >35d before 2020-01-31
            ]}},
        )
        with self.assertRaisesRegex(IndexMembershipError, r"002594.*enter"):
            resolver.resolve()

    def test_leave_when_still_active_raises(self) -> None:
        resolver = self._setup(
            [
                ("20220601", ["600015.SH", "600519.SH"]),
                ("20220630", ["600015.SH", "600519.SH"]),  # still present
            ],
            {"index_membership_cases": {"csi300": [
                {"ticker": "SH600015", "action": "leave",
                 "date": "2022-06-13"},
            ]}},
        )
        with self.assertRaisesRegex(IndexMembershipError,
                                    r"still a member|open-ended"):
            resolver.resolve()

    def test_ticker_never_in_index_raises(self) -> None:
        resolver = self._setup(
            [
                ("20200131", ["600519.SH"]),
            ],
            {"index_membership_cases": {"csi300": [
                {"ticker": "SZ999999", "action": "enter",
                 "date": "2020-01-31"},
            ]}},
        )
        with self.assertRaisesRegex(IndexMembershipError,
                                    r"999999.*not in any resolved run"):
            resolver.resolve()

    def test_unknown_action_raises(self) -> None:
        resolver = self._setup(
            [
                ("20200131", ["600519.SH"]),
            ],
            {"index_membership_cases": {"csi300": [
                {"ticker": "SH600519", "action": "frobnicate",
                 "date": "2020-01-31"},
            ]}},
        )
        with self.assertRaisesRegex(IndexMembershipError, r"unknown action"):
            resolver.resolve()


class ConfigAndInputValidationTests(unittest.TestCase):

    def test_rejects_unknown_index_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(IndexMembershipError, r"Unknown index code"):
                IndexMembershipResolver(
                    tushare_dir=Path(tmp), output_dir=Path(tmp),
                    indices=("999999.SH",),
                )

    def test_missing_input_parquet_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resolver = IndexMembershipResolver(
                tushare_dir=Path(tmp), output_dir=Path(tmp),
                indices=("000300.SH",),
            )
            with self.assertRaisesRegex(IndexMembershipError, r"index_weight.*000300\.SH"):
                resolver.resolve()

    def test_empty_input_parquet_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            path = tmp_path / "index_weight" / "000300.SH.parquet"
            path.parent.mkdir(parents=True)
            pd.DataFrame({"con_code": [], "trade_date": []}).to_parquet(path)
            resolver = IndexMembershipResolver(
                tushare_dir=tmp_path, output_dir=tmp_path / "out",
                indices=("000300.SH",),
            )
            with self.assertRaisesRegex(IndexMembershipError, r"empty"):
                resolver.resolve()

    def test_missing_required_columns_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            path = tmp_path / "index_weight" / "000300.SH.parquet"
            path.parent.mkdir(parents=True)
            pd.DataFrame({"only_one_col": [1, 2, 3]}).to_parquet(path)
            resolver = IndexMembershipResolver(
                tushare_dir=tmp_path, output_dir=tmp_path / "out",
                indices=("000300.SH",),
            )
            with self.assertRaisesRegex(IndexMembershipError,
                                        r"missing required columns"):
                resolver.resolve()

    def test_missing_reference_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_index_weight_parquet(
                tmp_path / "index_weight" / "000300.SH.parquet",
                "000300.SH",
                [("20200131", ["600519.SH"])],
            )
            resolver = IndexMembershipResolver(
                tushare_dir=tmp_path, output_dir=tmp_path / "out",
                reference_cases_path=tmp_path / "absent.yaml",
                indices=("000300.SH",),
            )
            with self.assertRaisesRegex(IndexMembershipError, r"Reference cases file not found"):
                resolver.resolve()


class AtomicWriteTests(unittest.TestCase):

    def test_no_tmp_file_left_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_index_weight_parquet(
                tmp_path / "index_weight" / "000300.SH.parquet",
                "000300.SH",
                [("20200131", ["600519.SH"])],
            )
            IndexMembershipResolver(
                tushare_dir=tmp_path, output_dir=tmp_path / "out",
                indices=("000300.SH",),
            ).resolve()
            tmp_files = list((tmp_path / "out").glob("**/*.tmp"))
        self.assertEqual(tmp_files, [])


class ConstantsTests(unittest.TestCase):

    def test_qlib_open_end_date_is_design_value(self) -> None:
        self.assertEqual(QLIB_OPEN_END_DATE, "2099-12-31")

    def test_tolerance_is_one_monthly_snapshot(self) -> None:
        # Tushare returns ~monthly snapshots; 35d catches any intra-
        # month event without false matches against an unrelated nearby
        # snapshot.
        self.assertEqual(MEMBERSHIP_DATE_TOLERANCE_DAYS, 35)


if __name__ == "__main__":
    unittest.main()
