"""Tests for ``src.data.pit.pit_validator.PITValidator``.

These tests verify the report-aggregation surface only (CheckResult,
PITValidationReport, exit-code policy). The full end-to-end check
behaviors are exercised by the Phase B smoke test (real Tushare slice
+ real qlib.init), not by unit tests — bringing up a complete qlib
provider in a tempdir within a unit test pulls in too many qlib
internals for fast feedback.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.pit.pit_validator import (  # noqa: E402
    CheckResult,
    PITValidationReport,
    PITValidator,
    PITValidatorError,
    _in_calendar_range,
    _lookahead_violation,
    _suspension_signal,
)


def _qlib_frame(ticker: str, dates_closes: list[tuple[str, float]]) -> pd.DataFrame:
    """A qlib ``D.features``-shaped frame: MultiIndex [instrument, datetime],
    one ``$close`` column. Use float('nan') for a NaN close."""
    idx = pd.MultiIndex.from_tuples(
        [(ticker, pd.Timestamp(d)) for d, _ in dates_closes],
        names=["instrument", "datetime"],
    )
    return pd.DataFrame({"$close": [c for _, c in dates_closes]}, index=idx)


def _mean_frame(ticker: str, dates_vals: list[tuple[str, float]]) -> pd.DataFrame:
    """A qlib ``D.features(['Mean($close, 20)'])``-shaped frame (what check [D]
    queries). ``float('nan')`` for a NaN window value."""
    idx = pd.MultiIndex.from_tuples(
        [(ticker, pd.Timestamp(d)) for d, _ in dates_vals],
        names=["instrument", "datetime"],
    )
    return pd.DataFrame({"Mean($close, 20)": [v for _, v in dates_vals]}, index=idx)


class ExitCodeTests(unittest.TestCase):
    """Per the legacy survivorship convention: 0=clean, 1=warnings,
    2=any failure. The aggregation rule prefers the WORST status."""

    def test_all_clean_returns_zero(self) -> None:
        rep = PITValidationReport(
            checks=[CheckResult(name="x", code="A", passed=True)],
            provider_dir=Path("/dev/null"),
        )
        self.assertEqual(rep.exit_code, 0)

    def test_warnings_only_returns_one(self) -> None:
        rep = PITValidationReport(
            checks=[
                CheckResult(name="x", code="A", passed=True),
                CheckResult(name="y", code="E", passed=True,
                            warnings=["yaml deferred"]),
            ],
            provider_dir=Path("/dev/null"),
        )
        self.assertEqual(rep.exit_code, 1)

    def test_any_failure_returns_two(self) -> None:
        rep = PITValidationReport(
            checks=[
                CheckResult(name="x", code="A", passed=True),
                CheckResult(name="y", code="B", passed=False, errors=["bad"]),
                CheckResult(name="z", code="C", passed=True,
                            warnings=["minor"]),  # warning + failure -> fail wins
            ],
            provider_dir=Path("/dev/null"),
        )
        self.assertEqual(rep.exit_code, 2)


class ReportSerializationTests(unittest.TestCase):

    def test_to_dict_round_trips_basic_fields(self) -> None:
        rep = PITValidationReport(
            checks=[CheckResult(
                name="Survivorship", code="A", passed=False,
                errors=["err1", "err2"], warnings=["w1"],
                details={"sample_size": 5, "passes": 3},
            )],
            provider_dir=Path("/tmp/prov"),
        )
        d = rep.to_dict()
        self.assertEqual(d["exit_code"], 2)
        self.assertEqual(d["provider_dir"], str(Path("/tmp/prov")))
        self.assertEqual(len(d["checks"]), 1)
        c = d["checks"][0]
        self.assertEqual(c["code"], "A")
        self.assertEqual(c["errors"], ["err1", "err2"])
        self.assertEqual(c["warnings"], ["w1"])
        self.assertEqual(c["details"], {"sample_size": 5, "passes": 3})


class SanityCheckTests(unittest.TestCase):
    """``_sanity_check_provider`` validates that the target directory
    looks like a qlib provider before kicking off the full validation."""

    def test_rejects_missing_calendars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            validator = PITValidator(
                provider_dir=tmp_path,
                delisted_registry_path=tmp_path / "absent.parquet",
            )
            with self.assertRaisesRegex(PITValidatorError, r"calendars/day\.txt"):
                validator._sanity_check_provider()

    def test_rejects_missing_instruments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "calendars").mkdir()
            (tmp_path / "calendars" / "day.txt").write_text("2020-01-01\n")
            validator = PITValidator(
                provider_dir=tmp_path,
                delisted_registry_path=tmp_path / "absent.parquet",
            )
            with self.assertRaisesRegex(PITValidatorError, r"instruments/all\.txt"):
                validator._sanity_check_provider()

    def test_rejects_missing_features(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "calendars").mkdir()
            (tmp_path / "calendars" / "day.txt").write_text("2020-01-01\n")
            (tmp_path / "instruments").mkdir()
            (tmp_path / "instruments" / "all.txt").write_text("")
            validator = PITValidator(
                provider_dir=tmp_path,
                delisted_registry_path=tmp_path / "absent.parquet",
            )
            with self.assertRaisesRegex(PITValidatorError, r"features/"):
                validator._sanity_check_provider()


class DelistVerdictHelperTests(unittest.TestCase):
    """PR #272 — pure verdict helpers. Look-ahead (data past delist) is the
    only hard failure; missing/short near delist is a suspension WARNING."""

    DELIST = pd.Timestamp("2022-06-01")

    def test_in_calendar_range(self) -> None:
        lo, hi = pd.Timestamp("2018-01-02"), pd.Timestamp("2026-01-16")
        self.assertTrue(_in_calendar_range(pd.Timestamp("2022-06-01"), lo, hi))
        self.assertTrue(_in_calendar_range(lo, lo, hi))   # boundary inclusive
        self.assertTrue(_in_calendar_range(hi, lo, hi))
        self.assertFalse(_in_calendar_range(pd.Timestamp("2017-12-31"), lo, hi))
        self.assertFalse(_in_calendar_range(pd.Timestamp("2026-02-01"), lo, hi))

    def test_lookahead_violation_flags_nonnan_after_delist(self) -> None:
        df = _qlib_frame("X", [("2022-05-30", 9.0), ("2022-06-02", 9.5)])  # 06-02 > delist
        msg = _lookahead_violation(df, self.DELIST, "X")
        self.assertIsNotNone(msg)
        self.assertIn("look-ahead", msg)

    def test_lookahead_ignores_nan_after_and_data_up_to_delist(self) -> None:
        # NaN after delist → fine; data ending at/ before delist → fine.
        nan_after = _qlib_frame("X", [("2022-05-30", 9.0), ("2022-06-02", float("nan"))])
        self.assertIsNone(_lookahead_violation(nan_after, self.DELIST, "X"))
        upto = _qlib_frame("X", [("2022-05-30", 9.0), ("2022-06-01", 9.1)])
        self.assertIsNone(_lookahead_violation(upto, self.DELIST, "X"))
        self.assertIsNone(_lookahead_violation(None, self.DELIST, "X"))
        self.assertIsNone(_lookahead_violation(pd.DataFrame(), self.DELIST, "X"))

    def test_suspension_signal_missing_and_allnan_and_truncation(self) -> None:
        ws, we = "2022-05-02", "2022-06-11"
        # empty / None → "no data"
        self.assertIn("no data", _suspension_signal(None, self.DELIST, ws, we, "X", 7))
        self.assertIn("no data", _suspension_signal(pd.DataFrame(), self.DELIST, ws, we, "X", 7))
        # all-NaN
        allnan = _qlib_frame("X", [("2022-05-30", float("nan"))])
        self.assertIn("all-NaN", _suspension_signal(allnan, self.DELIST, ws, we, "X", 7))
        # last trade 60d before delist → truncation/suspension warning
        trunc = _qlib_frame("X", [("2022-04-01", 9.0)])
        msg = _suspension_signal(trunc, self.DELIST, ws, we, "X", 7)
        self.assertIsNotNone(msg)
        self.assertIn("suspended before formal delist", msg)

    def test_suspension_signal_none_when_data_reaches_delist(self) -> None:
        ok = _qlib_frame("X", [("2022-05-30", 9.0), ("2022-06-01", 9.1)])
        self.assertIsNone(_suspension_signal(ok, self.DELIST, "2022-05-02", "2022-06-11", "X", 7))


class CheckBClassificationTests(unittest.TestCase):
    """PR #272 — the [B] sweep glue: out-of-range delistings are skipped, a
    pre-delist suspension is a WARNING (build still swaps), and only look-ahead
    is a hard ERROR (blocks the swap)."""

    def _validator(self, tmp_path: Path) -> PITValidator:
        (tmp_path / "calendars").mkdir()
        # clean 2018..2026 calendar (endpoints only — range is what matters)
        (tmp_path / "calendars" / "day.txt").write_text(
            "2018-01-02\n2026-01-16\n", encoding="utf-8"
        )
        (tmp_path / "instruments").mkdir()
        (tmp_path / "instruments" / "all.txt").write_text("", encoding="utf-8")
        (tmp_path / "features").mkdir()
        return PITValidator(
            provider_dir=tmp_path,
            delisted_registry_path=tmp_path / "reg.parquet",
        )

    def test_classification(self) -> None:
        import importlib.util
        if importlib.util.find_spec("qlib") is None:
            self.skipTest("qlib not installed")
        registry = pd.DataFrame({
            "ticker": ["PRE", "POST", "LOOKAHEAD", "SUSPENDED", "FAITHFUL"],
            "delist_date": [
                pd.Timestamp("2017-06-01"),   # out of range (before cal_start)
                pd.Timestamp("2027-01-01"),   # out of range (after cal_end)
                pd.Timestamp("2022-06-01"),   # look-ahead → ERROR
                pd.Timestamp("2022-06-01"),   # suspended 60d early → WARNING
                pd.Timestamp("2022-06-01"),   # faithful → clean
            ],
        })

        def fake_features(insts, fields, start, end):  # type: ignore[no-untyped-def]
            tk = insts[0]
            if tk == "LOOKAHEAD":
                return _qlib_frame(tk, [("2022-05-30", 9.0), ("2022-06-02", 9.5)])
            if tk == "SUSPENDED":
                return _qlib_frame(tk, [("2022-04-01", 9.0)])  # 61d before delist
            if tk == "FAITHFUL":
                return _qlib_frame(tk, [("2022-05-30", 9.0), ("2022-06-01", 9.1)])
            raise AssertionError(f"out-of-range ticker {tk} should not be queried")

        fake_d = mock.Mock()
        fake_d.features.side_effect = fake_features
        with tempfile.TemporaryDirectory() as tmp:
            validator = self._validator(Path(tmp))
            # D is a lazy qlib Wrapper (no .features until init); replace the
            # whole object so the method's `from qlib.data import D` binds ours.
            with mock.patch("qlib.data.D", fake_d):
                result = validator._check_b_delist_boundary(registry)

        # PRE/POST never queried (skipped before the D.features call).
        self.assertEqual(result.details["out_of_range_skipped"], 2)
        self.assertEqual(result.details["checked"], 3)
        # LOOKAHEAD is the only hard failure.
        self.assertFalse(result.passed)
        self.assertEqual(result.details["violation_count"], 1)
        self.assertTrue(any("look-ahead" in e for e in result.errors))
        # SUSPENDED is a warning, not a failure.
        self.assertEqual(result.details["suspension_warnings"], 1)
        self.assertTrue(any("suspended before formal delist" in w for w in result.warnings))


class CheckDInRangeTests(unittest.TestCase):
    """PR #272 P3 follow-up — [D]'s min_periods budget (3 tickers) must be
    sampled from the IN-RANGE registry, mirroring [A]/[B]. With out-of-range
    delistings listed first, the unfiltered ``head(3)`` spent the whole budget
    on tickers whose delist+1..+20 window is empty (``df.empty`` → ``continue``)
    → the load-bearing §4.3.2 assertion silently became a no-op."""

    def _validator(self, tmp_path: Path) -> PITValidator:
        (tmp_path / "calendars").mkdir()
        # clean 2018..2026 calendar (endpoints only — range is what matters)
        (tmp_path / "calendars" / "day.txt").write_text(
            "2018-01-02\n2026-01-16\n", encoding="utf-8"
        )
        (tmp_path / "instruments").mkdir()
        (tmp_path / "instruments" / "all.txt").write_text("", encoding="utf-8")
        (tmp_path / "features").mkdir()
        return PITValidator(
            provider_dir=tmp_path,
            delisted_registry_path=tmp_path / "reg.parquet",
        )

    def test_samples_in_range_and_skips_out_of_range(self) -> None:
        import importlib.util
        if importlib.util.find_spec("qlib") is None:
            self.skipTest("qlib not installed")
        # Out-of-range delistings listed FIRST — the exact ordering that masked
        # the no-op (prod registry happened to list in-range rows first). The
        # 3-ticker budget must skip these and reach the 2 in-range rows.
        registry = pd.DataFrame({
            "ticker": ["PRE_A", "PRE_B", "PRE_C", "OK", "VIOLATION"],
            "delist_date": [
                pd.Timestamp("2015-06-01"),   # out of range (before cal_start)
                pd.Timestamp("2016-06-01"),   # out of range
                pd.Timestamp("2017-06-01"),   # out of range
                pd.Timestamp("2022-06-01"),   # in range → Mean all-NaN → clean
                pd.Timestamp("2022-06-01"),   # in range → Mean non-NaN → §4.3.2
            ],
        })

        def fake_features(insts, fields, start, end):  # type: ignore[no-untyped-def]
            tk = insts[0]
            if tk == "OK":
                return _mean_frame(tk, [("2022-06-02", float("nan"))])
            if tk == "VIOLATION":
                return _mean_frame(tk, [("2022-06-02", 9.5)])
            raise AssertionError(f"out-of-range ticker {tk} should not be queried")

        fake_d = mock.Mock()
        fake_d.features.side_effect = fake_features
        with tempfile.TemporaryDirectory() as tmp:
            validator = self._validator(Path(tmp))
            # D is a lazy qlib Wrapper (no .features until init); replace the
            # whole object so the method's `from qlib.data import D` binds ours.
            with mock.patch("qlib.data.D", fake_d):
                result = validator._check_d_qlib_operator_min_periods(registry)

        # The 3 out-of-range rows are skipped before any D.features call; only
        # the 2 in-range rows are examined and both run a real assertion.
        self.assertEqual(result.details["out_of_range_skipped"], 3)
        self.assertEqual(result.details["in_range_total"], 2)
        self.assertEqual(result.details["examined"], 2)
        self.assertEqual(result.details["checked"], 2)
        # The in-range VIOLATION is caught — proof the budget actually ran the
        # min_periods assertion instead of no-op'ing on empty out-of-range
        # frames (the pre-fix failure mode).
        self.assertFalse(result.passed)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("§4.3.2", result.errors[0])

    def test_tail_delisting_with_empty_window_does_not_consume_budget(self) -> None:
        # Codex P2 on #273: an IN-RANGE delisting at the calendar TAIL whose
        # delist+1..+20 window falls past cal_end returns an empty frame. Such
        # rows must NOT consume the budget — the loop keeps scanning until 3
        # NON-EMPTY checks run. Here 3 tail rows precede the real OK/VIOLATION
        # rows; the §4.3.2 violation must still be caught. (With the old
        # head(3), the 3 tail rows would no-op the whole check.)
        import importlib.util
        if importlib.util.find_spec("qlib") is None:
            self.skipTest("qlib not installed")
        registry = pd.DataFrame({
            "ticker": ["TAIL1", "TAIL2", "TAIL3", "OK", "VIOLATION"],
            "delist_date": [
                pd.Timestamp("2026-01-12"),  # in range, but +1..+20 past cal_end
                pd.Timestamp("2026-01-13"),
                pd.Timestamp("2026-01-14"),
                pd.Timestamp("2022-06-01"),  # in range → Mean all-NaN → clean
                pd.Timestamp("2022-06-01"),  # in range → Mean non-NaN → §4.3.2
            ],
        })

        def fake_features(insts, fields, start, end):  # type: ignore[no-untyped-def]
            tk = insts[0]
            if tk.startswith("TAIL"):
                return pd.DataFrame()  # no calendar coverage past cal_end
            if tk == "OK":
                return _mean_frame(tk, [("2022-06-02", float("nan"))])
            if tk == "VIOLATION":
                return _mean_frame(tk, [("2022-06-02", 9.5)])
            raise AssertionError(f"unexpected ticker {tk}")

        fake_d = mock.Mock()
        fake_d.features.side_effect = fake_features
        with tempfile.TemporaryDirectory() as tmp:
            validator = self._validator(Path(tmp))
            with mock.patch("qlib.data.D", fake_d):
                result = validator._check_d_qlib_operator_min_periods(registry)

        # All 5 in-range rows examined; only OK + VIOLATION ran a real assertion.
        self.assertEqual(result.details["out_of_range_skipped"], 0)
        self.assertEqual(result.details["in_range_total"], 5)
        self.assertEqual(result.details["examined"], 5)
        self.assertEqual(result.details["checked"], 2)
        # VIOLATION caught despite 3 tail rows first — the budget was not wasted.
        self.assertFalse(result.passed)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("§4.3.2", result.errors[0])


if __name__ == "__main__":
    unittest.main()
