"""Tests for the P3-4b fetch_manifest read / write / merge / clear.

Mock + synthetic manifests only — no real fetch, no network. The merge-precision
RED LINE (a self-healed hole is dropped, an un-healed hole is never silently
removed) is pinned by two independent counter-example tests:
``test_counterexample_does_not_wrongly_remove_unhealed_other_endpoint`` (the
"误删 = silent partial" case) and ``test_counterexample_healed_hole_does_not_linger``
(the "赖着 = false alarm" case).
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.tushare.client import TushareClientError  # noqa: E402
from src.data.tushare.fetch_manifest import (  # noqa: E402
    MANIFEST_FILENAME,
    SCHEMA_VERSION,
    FetchManifestError,
    build_manifest,
    clear_manifest,
    merge_manifest,
    read_manifest,
    write_manifest,
)
from src.data.tushare.fetcher import FetchHole, TushareFetchResult  # noqa: E402

FIXED_NOW = datetime(2026, 6, 9, 4, 30, 0, tzinfo=timezone.utc)


def _hole(endpoint, unit, *, reason_class="transient", attempts=5, last_error="err"):
    return FetchHole(
        endpoint=endpoint, unit=unit, reason_class=reason_class,
        attempts=attempts, last_error=last_error,
    )


def _result(endpoint, files_written=0):
    return TushareFetchResult(endpoint, files_written, 0, 0)


def _bm(results, holes, end="20251231", *, start="20180101", now=None):
    """Thin wrapper over build_manifest defaulting the coverage start, so same-
    range tests stay terse; narrower-scope tests pass start=/end= explicitly."""
    return build_manifest(results, holes, start, end, now=now)


class BuildAndWriteTests(unittest.TestCase):

    def test_build_fields_and_injected_timestamp(self) -> None:
        results = [_result("daily", 100), _result("namechange", 1)]
        holes = (_hole("daily", "ts_code=600001.SH year=2020", attempts=5),)
        m = _bm(results, holes, "20251231", now=FIXED_NOW)

        self.assertEqual(m.schema_version, SCHEMA_VERSION)
        self.assertEqual(m.fetched_at, FIXED_NOW.isoformat())  # injected, not wall-clock
        self.assertEqual(set(m.endpoints), {"daily", "namechange"})
        self.assertEqual(m.endpoints["daily"].status, "holes")
        self.assertEqual(m.endpoints["daily"].units_written, 100)
        self.assertEqual(m.endpoints["daily"].coverage_end_date, "20251231")
        self.assertEqual(len(m.endpoints["daily"].holes), 1)
        self.assertEqual(m.endpoints["namechange"].status, "complete")
        self.assertEqual(m.endpoints["namechange"].holes, ())

    def test_write_then_read_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / MANIFEST_FILENAME
            holes = (_hole(
                "daily", "ts_code=X year=2020", reason_class="transient",
                attempts=5, last_error="TushareClientError: rate limit",
            ),)
            m = _bm([_result("daily", 7)], holes, "20251231", now=FIXED_NOW)
            write_manifest(path, m)

            self.assertTrue(path.exists())
            back = read_manifest(path)
            self.assertIsNotNone(back)
            assert back is not None
            self.assertEqual(back.fetched_at, FIXED_NOW.isoformat())
            self.assertEqual(back.endpoints["daily"].holes[0].unit, "ts_code=X year=2020")
            self.assertEqual(back.endpoints["daily"].holes[0].attempts, 5)
            self.assertEqual(back.endpoints["daily"].holes[0].endpoint, "daily")
            # valid JSON on disk with the version stamp
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["schema_version"], SCHEMA_VERSION)

    def test_default_timestamp_is_system_clock(self) -> None:
        # now=None → system clock, NOT the fixed sentinel (just assert it differs
        # and is a non-empty ISO string).
        m = _bm([_result("daily", 1)], (), "20251231")
        self.assertTrue(m.fetched_at)
        self.assertNotEqual(m.fetched_at, FIXED_NOW.isoformat())


class AtomicWriteTests(unittest.TestCase):

    def test_failed_replace_leaves_prev_manifest_intact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / MANIFEST_FILENAME
            old = _bm([_result("daily", 1)], (), "20231231", now=FIXED_NOW)
            write_manifest(path, old)  # establish the prior value

            new = _bm(
                [_result("daily", 2)], (), "20251231",
                now=datetime(2027, 1, 1, tzinfo=timezone.utc),
            )
            with patch(
                "src.data.tushare.fetch_manifest.os.replace",
                side_effect=OSError("simulated crash before rename"),
            ):
                with self.assertRaises(OSError):
                    write_manifest(path, new)

            # The final manifest is STILL the old one — os.replace never swapped
            # the new file in, so no half-written / corrupt manifest is exposed.
            reloaded = read_manifest(path)
            assert reloaded is not None
            self.assertEqual(reloaded.endpoints["daily"].coverage_end_date, "20231231")
            self.assertEqual(reloaded.fetched_at, FIXED_NOW.isoformat())
            json.loads(path.read_text(encoding="utf-8"))  # still valid JSON, not truncated

    def test_no_tmp_left_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / MANIFEST_FILENAME
            write_manifest(
                path, _bm([_result("daily", 1)], (), "20251231", now=FIXED_NOW),
            )
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])


class ReadTests(unittest.TestCase):

    def test_missing_manifest_is_fresh_not_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_manifest(Path(tmp) / MANIFEST_FILENAME))

    def test_unknown_schema_version_fails_loud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / MANIFEST_FILENAME
            path.write_text(
                json.dumps({"schema_version": 999, "fetched_at": "x", "endpoints": {}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(FetchManifestError, "schema_version"):
                read_manifest(path)

    def test_missing_schema_version_fails_loud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / MANIFEST_FILENAME
            path.write_text(
                json.dumps({"fetched_at": "x", "endpoints": {}}), encoding="utf-8",
            )
            with self.assertRaisesRegex(FetchManifestError, "schema_version"):
                read_manifest(path)

    def test_malformed_json_fails_loud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / MANIFEST_FILENAME
            path.write_text("{not valid json", encoding="utf-8")
            with self.assertRaises(FetchManifestError):
                read_manifest(path)

    def test_missing_endpoints_member_fails_loud(self) -> None:
        # codex P2: valid version but NO `endpoints` member → must fail loud, not
        # silently parse as an empty manifest (which the next merge would treat as
        # "no prior holes" and erase recorded non-run holes).
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / MANIFEST_FILENAME
            path.write_text(
                json.dumps({"schema_version": 1, "fetched_at": "x"}), encoding="utf-8",
            )
            with self.assertRaisesRegex(FetchManifestError, "malformed"):
                read_manifest(path)

    def test_missing_endpoint_field_fails_loud(self) -> None:
        # an endpoint entry missing a required key (here coverage_start_date) →
        # fail loud rather than silently dropping/zeroing it.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / MANIFEST_FILENAME
            path.write_text(
                json.dumps({
                    "schema_version": 1, "fetched_at": "x",
                    "endpoints": {"daily": {
                        "status": "complete", "coverage_end_date": "20251231",
                        "units_written": 1, "holes": [],
                    }},
                }),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(FetchManifestError, "malformed"):
                read_manifest(path)

    def test_non_object_manifest_fails_loud(self) -> None:
        # codex P2: valid JSON that is not an object (e.g. a list) must fail loud,
        # not raise AttributeError from `.get(...)` outside the fail-loud path.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / MANIFEST_FILENAME
            path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
            with self.assertRaisesRegex(FetchManifestError, "not a JSON object"):
                read_manifest(path)

    def test_non_utf8_manifest_fails_loud(self) -> None:
        # codex P2: a corrupt / non-UTF-8 manifest makes read_text raise
        # UnicodeDecodeError before json.loads — it must fail loud as a
        # FetchManifestError, not escape as a traceback.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / MANIFEST_FILENAME
            path.write_bytes(b"\xff\xfe\x00 not valid utf-8 \x80\x81")
            with self.assertRaisesRegex(FetchManifestError, "unreadable"):
                read_manifest(path)


class MergeTests(unittest.TestCase):

    def test_prev_none_returns_current(self) -> None:
        cur = _bm([_result("daily", 1)], (), "20251231", now=FIXED_NOW)
        self.assertIs(merge_manifest(None, cur), cur)

    def test_self_healed_hole_is_dropped(self) -> None:
        # prev holed X@2020; this run ran daily and did NOT hole X@2020 → healed.
        prev = _bm(
            [_result("daily", 1)], (_hole("daily", "ts_code=X year=2020"),),
            "20251231", now=FIXED_NOW,
        )
        cur = _bm([_result("daily", 2)], (), "20251231", now=FIXED_NOW)
        merged = merge_manifest(prev, cur)
        self.assertEqual(merged.endpoints["daily"].holes, ())
        self.assertEqual(merged.endpoints["daily"].status, "complete")

    def test_unhealed_hole_stays_with_attempts_accumulated(self) -> None:
        # prev holed X@2020 (5 attempts); this run holes X@2020 again (5 attempts).
        prev = _bm(
            [_result("daily", 0)], (_hole("daily", "ts_code=X year=2020", attempts=5),),
            "20251231", now=FIXED_NOW,
        )
        cur = _bm(
            [_result("daily", 0)], (_hole("daily", "ts_code=X year=2020", attempts=5),),
            "20251231", now=FIXED_NOW,
        )
        merged = merge_manifest(prev, cur)
        kept = merged.endpoints["daily"].holes
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].unit, "ts_code=X year=2020")
        self.assertEqual(kept[0].attempts, 10)  # 5 + 5 accumulated across runs

    def test_coverage_end_date_advances_on_wider_run(self) -> None:
        # a same-or-wider run advances coverage_end_date (a NARROWER run is
        # refused by the guard — see test_narrower_scope_merge_is_refused).
        prev = _bm([_result("daily", 1)], (), start="20180101", end="20231231")
        cur = _bm([_result("daily", 1)], (), start="20180101", end="20251231")
        merged = merge_manifest(prev, cur)
        self.assertEqual(merged.endpoints["daily"].coverage_end_date, "20251231")
        self.assertEqual(merged.endpoints["daily"].coverage_start_date, "20180101")

    # ---- merge-precision RED LINE: two independent counter-examples ----

    def test_counterexample_does_not_wrongly_remove_unhealed_other_endpoint(self) -> None:
        # "误删 = silent partial": prev has holes in BOTH suspend_d and daily.
        # This run ran ONLY daily (and healed it). suspend_d did NOT run, so its
        # hole MUST be preserved — dropping it would silently mark an incomplete
        # endpoint complete.
        prev = _bm(
            [_result("daily", 0), _result("suspend_d", 0)],
            (
                _hole("daily", "ts_code=X year=2020"),
                _hole("suspend_d", "file"),
            ),
            "20251231", now=FIXED_NOW,
        )
        cur = _bm([_result("daily", 5)], (), "20251231", now=FIXED_NOW)
        merged = merge_manifest(prev, cur)

        self.assertEqual(merged.endpoints["daily"].holes, ())  # daily healed
        # suspend_d untouched — never silently removed for an endpoint that did not run
        self.assertIn("suspend_d", merged.endpoints)
        self.assertEqual(len(merged.endpoints["suspend_d"].holes), 1)
        self.assertEqual(merged.endpoints["suspend_d"].holes[0].unit, "file")
        self.assertEqual(merged.endpoints["suspend_d"].status, "holes")

    def test_counterexample_healed_hole_does_not_linger(self) -> None:
        # "赖着 = false alarm": prev holed X@2020 AND Y@2021 in daily. This run
        # heals X@2020 but Y@2021 still holes. Merge must DROP X@2020 (gone) and
        # KEEP Y@2021 — precise per-unit, not all-or-nothing in either direction.
        prev = _bm(
            [_result("daily", 0)],
            (
                _hole("daily", "ts_code=X year=2020"),
                _hole("daily", "ts_code=Y year=2021"),
            ),
            "20251231", now=FIXED_NOW,
        )
        cur = _bm(
            [_result("daily", 1)],
            (_hole("daily", "ts_code=Y year=2021"),),  # only Y still holes; X healed
            "20251231", now=FIXED_NOW,
        )
        merged = merge_manifest(prev, cur)

        units = {h.unit for h in merged.endpoints["daily"].holes}
        self.assertEqual(units, {"ts_code=Y year=2021"})  # X gone (not lingering), Y kept

    def test_narrower_scope_merge_is_refused(self) -> None:
        # codex P1: prev covered 2018-2025 with a daily hole at 2020. A re-run
        # scoped only to 2025 never re-attempts the 2020 unit, so treating its
        # absence from this run's holes as self-healed would silently drop it.
        # The merge REFUSES a narrower-scope date-scoped merge.
        prev = _bm(
            [_result("daily", 0)], (_hole("daily", "ts_code=X year=2020"),),
            start="20180101", end="20251231",
        )
        narrower = _bm([_result("daily", 1)], (), start="20250101", end="20251231")
        with self.assertRaisesRegex(FetchManifestError, "narrower-scope"):
            merge_manifest(prev, narrower)

    def test_wider_or_equal_scope_merge_self_heals(self) -> None:
        # a same-or-wider range re-attempts every prior hole → self-heal is safe.
        prev = _bm(
            [_result("daily", 0)], (_hole("daily", "ts_code=X year=2020"),),
            start="20180101", end="20251231",
        )
        wider = _bm([_result("daily", 1)], (), start="20170101", end="20261231")
        merged = merge_manifest(prev, wider)  # does not raise
        self.assertEqual(merged.endpoints["daily"].holes, ())  # healed
        self.assertEqual(merged.endpoints["daily"].coverage_start_date, "20170101")
        self.assertEqual(merged.endpoints["daily"].coverage_end_date, "20261231")

    def test_stock_basic_narrower_scope_is_not_refused(self) -> None:
        # stock_basic is date-agnostic (re-fetches the whole universe), so a
        # narrower date range does NOT refuse it — its holes always re-attempt.
        prev = _bm(
            [_result("stock_basic", 0)],
            (_hole("stock_basic", "list_status=L (active_stocks)"),),
            start="20180101", end="20251231",
        )
        narrower = _bm([_result("stock_basic", 2)], (), start="20250101", end="20251231")
        merged = merge_manifest(prev, narrower)  # does not raise
        self.assertEqual(merged.endpoints["stock_basic"].holes, ())  # healed

    def test_namechange_narrower_scope_is_refused(self) -> None:
        # codex P1-A: namechange is a date-scoped aggregate endpoint, so it MUST be
        # in the narrower-scope guard set (its hole unit is the stable "file"; the
        # range it covers lives in coverage_start/end, which the guard compares).
        prev = _bm(
            [_result("namechange", 0)],
            (_hole("namechange", "file"),),
            start="20180101", end="20251231",
        )
        narrower = _bm([_result("namechange", 1)], (), start="20250101", end="20251231")
        with self.assertRaisesRegex(FetchManifestError, "narrower-scope"):
            merge_manifest(prev, narrower)

    def test_skipped_aggregate_does_not_advance_coverage(self) -> None:
        # codex P1-B: a wider run that SKIPS a prior narrow aggregate file (nothing
        # written, units_written=0) must NOT claim the wider coverage — the data on
        # disk is still the narrow file. Coverage stays the actually-fetched range.
        prev = _bm([_result("namechange", 1)], (), start="20200101", end="20251231")
        wider_skip = _bm([_result("namechange", 0)], (), start="20180101", end="20251231")
        merged = merge_manifest(prev, wider_skip)
        self.assertEqual(merged.endpoints["namechange"].coverage_start_date, "20200101")
        self.assertEqual(merged.endpoints["namechange"].coverage_end_date, "20251231")

    def test_first_run_skipped_endpoint_claims_empty_coverage(self) -> None:
        # codex P2: on the FIRST manifest (no prior to fall back to), an endpoint
        # entirely SKIPPED by resume (pre-existing file, units_written=0, no holes)
        # must NOT claim the requested range — its coverage is empty (this run
        # established none), so a gate cannot mistake a stale narrow dump for it.
        first_skip = _bm([_result("namechange", 0)], (), start="20180101", end="20251231")
        merged = merge_manifest(None, first_skip)
        self.assertEqual(merged.endpoints["namechange"].coverage_start_date, "")
        self.assertEqual(merged.endpoints["namechange"].coverage_end_date, "")
        # a written endpoint on first run DOES record the range
        first_written = _bm([_result("namechange", 1)], (), start="20180101", end="20251231")
        nm = merge_manifest(None, first_written).endpoints["namechange"]
        self.assertEqual(nm.coverage_start_date, "20180101")
        self.assertEqual(nm.coverage_end_date, "20251231")

    def test_written_run_advances_coverage(self) -> None:
        # the complement: when the run DID write (units_written > 0), coverage
        # spans the widest range (a genuinely wider fetch is recorded).
        prev = _bm([_result("daily", 1)], (), start="20200101", end="20231231")
        wider_written = _bm([_result("daily", 9)], (), start="20180101", end="20251231")
        merged = merge_manifest(prev, wider_written)
        self.assertEqual(merged.endpoints["daily"].coverage_start_date, "20180101")
        self.assertEqual(merged.endpoints["daily"].coverage_end_date, "20251231")

    def test_hole_free_narrower_run_is_allowed(self) -> None:
        # the guard fires only when there are prior holes to wrongly drop; a
        # hole-free narrower run is harmless and proceeds without regressing
        # coverage (the wider actually-fetched range is kept).
        prev = _bm([_result("daily", 5)], (), start="20180101", end="20251231")  # no holes
        narrower = _bm([_result("daily", 0)], (), start="20250101", end="20251231")
        merged = merge_manifest(prev, narrower)  # does NOT raise
        self.assertEqual(merged.endpoints["daily"].coverage_start_date, "20180101")
        self.assertEqual(merged.endpoints["daily"].coverage_end_date, "20251231")

    def test_index_weight_hole_with_stable_unit_survives_rerun(self) -> None:
        # codex P1: index_weight holes use a STABLE per-index unit, so a re-run
        # that re-fails the index (at whatever first-failing year) produces the
        # SAME unit — the prior hole is KEPT (attempts accumulated), never dropped
        # as falsely self-healed while the index file is still missing.
        prev = _bm(
            [_result("index_weight", 0)],
            (_hole("index_weight", "index=000300.SH", attempts=5),),
            start="20200101", end="20231231",
        )
        cur = _bm(
            [_result("index_weight", 0)],
            (_hole("index_weight", "index=000300.SH", attempts=5),),
            start="20200101", end="20231231",
        )
        merged = merge_manifest(prev, cur)
        kept = merged.endpoints["index_weight"].holes
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].unit, "index=000300.SH")
        self.assertEqual(kept[0].attempts, 10)  # accumulated → kept, not dropped

    def test_aggregate_hole_survives_wider_rerun(self) -> None:
        # codex P2: namechange / suspend_d holes use a stable "file" unit, so a
        # WIDER re-run that fails again matches the prior hole — attempts
        # accumulate, the hole is NOT dropped/reset just because the requested
        # range changed (its coverage advances, but the hole identity is stable).
        prev = _bm(
            [_result("namechange", 0)], (_hole("namechange", "file", attempts=5),),
            start="20200101", end="20201231",
        )
        wider = _bm(
            [_result("namechange", 0)], (_hole("namechange", "file", attempts=5),),
            start="20180101", end="20251231",
        )
        merged = merge_manifest(prev, wider)
        kept = merged.endpoints["namechange"].holes
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].unit, "file")
        self.assertEqual(kept[0].attempts, 10)  # accumulated, not reset


class CoverageTruthfulnessTests(unittest.TestCase):
    """P3-7b: coverage + holes must together reflect the REAL state of every
    unit — no fabricated range union, no empty-string sentinel poisoning the
    date arithmetic, no hole loss from a run that established nothing."""

    def test_disjoint_coverage_merge_refused_forward_gap(self) -> None:
        prev = _bm([_result("daily", 3)], (), start="20000101", end="20101231")
        cur = _bm([_result("daily", 3)], (), start="20200101", end="20251231")
        with self.assertRaisesRegex(FetchManifestError, "disjoint"):
            merge_manifest(prev, cur)

    def test_disjoint_coverage_merge_refused_backward_gap(self) -> None:
        prev = _bm([_result("daily", 3)], (), start="20200101", end="20251231")
        cur = _bm([_result("daily", 3)], (), start="20000101", end="20101231")
        with self.assertRaisesRegex(FetchManifestError, "disjoint"):
            merge_manifest(prev, cur)

    def test_adjacent_coverage_merges(self) -> None:
        # Gap of exactly one calendar day (Dec 31 → Jan 1) is contiguous.
        prev = _bm([_result("daily", 3)], (), start="20000101", end="20251231")
        cur = _bm([_result("daily", 3)], (), start="20260101", end="20260611")
        merged = merge_manifest(prev, cur)
        cov = merged.endpoints["daily"]
        self.assertEqual(
            (cov.coverage_start_date, cov.coverage_end_date),
            ("20000101", "20260611"),
        )

    def test_empty_prev_sentinel_does_not_poison_minmax(self) -> None:
        # A first manifest over a pre-existing dump records "" (coverage not
        # established). A later run that writes must take ITS dates — "" sorts
        # before every date and would otherwise stick forever.
        prev = _bm([_result("daily", 0)], ())  # skipped → "" coverage
        cur = _bm([_result("daily", 2)], (), start="20180101", end="20251231")
        merged = merge_manifest(prev, cur)
        cov = merged.endpoints["daily"]
        self.assertEqual(
            (cov.coverage_start_date, cov.coverage_end_date),
            ("20180101", "20251231"),
        )

    def test_noop_run_preserves_prev_endpoint_verbatim(self) -> None:
        # An endpoint that ran but wrote nothing, holed nothing, and
        # established no coverage is a manifest no-op: the prior record —
        # including its HOLES — is preserved, not "self-healed" away. (Also
        # pins that an unestablished current does not trip the narrower-scope
        # refusal via the "" sentinel.)
        prev = _bm(
            [_result("daily", 5)],
            (_hole("daily", "ts_code=600000.SH year=2020"),),
            start="20180101", end="20251231",
        )
        cur = _bm([_result("daily", 0)], ())  # nothing established
        merged = merge_manifest(prev, cur)
        self.assertEqual(merged.endpoints["daily"], prev.endpoints["daily"])


class ClearTests(unittest.TestCase):

    def test_clear_removes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / MANIFEST_FILENAME
            write_manifest(
                path, _bm([_result("daily", 1)], (), "20251231", now=FIXED_NOW),
            )
            self.assertTrue(path.exists())
            clear_manifest(path)
            self.assertFalse(path.exists())
            self.assertIsNone(read_manifest(path))  # gone → fresh

    def test_clear_missing_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / MANIFEST_FILENAME
            clear_manifest(path)  # no error
            self.assertFalse(path.exists())


class CliManifestIntegrationTests(unittest.TestCase):
    """End-to-end: 01_fetch_tushare writes fetch_manifest.json at run end, and a
    re-run self-heals a hole whose unit succeeds the second time (the merge red
    line proven through the real CLI, not just the merge function)."""

    @staticmethod
    def _load_cli():
        import importlib.util
        path = PROJECT_ROOT / "scripts" / "data_pipeline" / "01_fetch_tushare.py"
        spec = importlib.util.spec_from_file_location("_fetch01_manifest_under_test", path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @staticmethod
    def _seed_universe(out: Path, tickers: list[str]) -> None:
        half = len(tickers) // 2 or 1
        pd.DataFrame({"ts_code": tickers[:half]}).to_parquet(
            out / "active_stocks.parquet", index=False)
        pd.DataFrame({"ts_code": tickers[half:]}).to_parquet(
            out / "delisted_stocks.parquet", index=False)

    @staticmethod
    def _daily_row(ticker: str) -> pd.DataFrame:
        # trade_date is the year's LAST WEEKDAY so a written file counts as
        # complete under the P3-7b freshness rule (re-runs resume-skip it).
        return pd.DataFrame({
            "ts_code": [ticker], "trade_date": ["20251231"],
            "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
            "vol": [0.0], "amount": [0.0],
        })

    def test_manifest_written_and_self_heals_across_runs(self) -> None:
        mod = self._load_cli()
        good, bad = "600000.SH", "600001.SH"
        state = {"heal": False}
        daily_calls: list[str] = []

        def side_effect(api, **p):
            if api == "daily":
                daily_calls.append(p.get("ts_code"))
                if p.get("ts_code") == bad and not state["heal"]:
                    raise TushareClientError("returned None — rate limit exceeded")
            return self._daily_row(p.get("ts_code", "X"))

        client = MagicMock()
        client.call = MagicMock(side_effect=side_effect)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self._seed_universe(out, [good, bad])
            manifest_path = out / MANIFEST_FILENAME
            daily_dir = out / "daily" / "2025"
            args = [
                "--output-dir", str(out), "--endpoints", "daily",
                "--start-date", "20250101", "--end-date", "20251231",
                "--rate-limit-sleep-ms", "0",
            ]

            # Run 1: the bad ticker holes → exit 3, manifest records the hole.
            with patch("src.data.tushare.fetcher.time.sleep"), \
                    patch.object(mod.TushareClient, "from_environment", return_value=client):
                rc1 = mod.main(args)
            self.assertEqual(rc1, 3)
            # PREREQUISITE for self-heal: the hole left NO file on disk (not even
            # a `.tmp`), so resume WILL re-fetch it; the succeeded unit wrote its.
            self.assertTrue((daily_dir / f"{good}.parquet").exists())
            self.assertFalse((daily_dir / f"{bad}.parquet").exists())
            self.assertFalse((daily_dir / f"{bad}.parquet.tmp").exists())
            m1 = read_manifest(manifest_path)
            assert m1 is not None
            self.assertEqual(m1.endpoints["daily"].status, "holes")
            self.assertEqual(len(m1.endpoints["daily"].holes), 1)
            self.assertIn(bad, m1.endpoints["daily"].holes[0].unit)

            # Run 2: MUST go through real file-existence resume — the good unit's
            # file exists so it is SKIPPED (not re-called), the bad unit's file is
            # missing so it is re-fetched (now succeeds). Asserting the call set
            # proves resume is genuinely driving the re-fetch, not a mock bypass.
            state["heal"] = True
            daily_calls.clear()
            with patch("src.data.tushare.fetcher.time.sleep"), \
                    patch.object(mod.TushareClient, "from_environment", return_value=client):
                rc2 = mod.main(args)
            self.assertEqual(rc2, 0)
            self.assertEqual(daily_calls, [bad])  # ONLY the holed unit re-fetched
            self.assertTrue((daily_dir / f"{bad}.parquet").exists())  # healed on disk
            m2 = read_manifest(manifest_path)
            assert m2 is not None
            self.assertEqual(m2.endpoints["daily"].holes, ())  # self-healed
            self.assertEqual(m2.endpoints["daily"].status, "complete")

    def test_main_returns_1_on_narrower_scope_refusal(self) -> None:
        # codex P2: a narrower-scope rerun makes merge_manifest raise; main() must
        # catch it and return 1 cleanly, NOT escape as a traceback.
        mod = self._load_cli()
        bad = "600001.SH"

        def side_effect(api, **p):
            if api == "daily" and p.get("ts_code") == bad:
                raise TushareClientError("returned None — rate limit exceeded")
            return self._daily_row(p.get("ts_code", "X"))

        client = MagicMock()
        client.call = MagicMock(side_effect=side_effect)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self._seed_universe(out, ["600000.SH", bad])
            common = [
                "--output-dir", str(out), "--endpoints", "daily",
                "--rate-limit-sleep-ms", "0",
            ]
            with patch("src.data.tushare.fetcher.time.sleep"), \
                    patch.object(mod.TushareClient, "from_environment", return_value=client):
                # run 1: wide range, bad ticker holes → manifest records a daily
                # hole with coverage 2024-2025.
                rc1 = mod.main(common + ["--start-date", "20240101", "--end-date", "20251231"])
                self.assertEqual(rc1, 3)
                # run 2: NARROWER range → merge_manifest refuses → main returns 1
                # cleanly (no traceback escaping) AND — the P3-7b red line —
                # the manifest is left BYTE-FOR-BYTE as run 1 wrote it: the
                # refusal exists to PRESERVE the hole ledger, so it must never
                # be answered by deleting that ledger.
                manifest_path = out / MANIFEST_FILENAME
                bytes_before = manifest_path.read_bytes()
                rc2 = mod.main(common + ["--start-date", "20250101", "--end-date", "20251231"])
            self.assertEqual(rc2, 1)
            self.assertEqual(manifest_path.read_bytes(), bytes_before)

    def test_main_returns_1_and_keeps_manifest_on_write_oserror(self) -> None:
        # codex P2 + P3-7b red line: a manifest WRITE OSError (disk full /
        # permissions / rename failure) after a completed fetch must surface as
        # a clean exit 1, not a traceback — and the PRIOR manifest is left
        # byte-for-byte intact (it still truthfully describes the units it
        # recorded; the non-zero exit stops any orchestrated build, and the
        # freshness rule re-attempts whatever this run changed next time).
        mod = self._load_cli()
        client = MagicMock()
        client.call = MagicMock(
            side_effect=lambda api, **p: self._daily_row(p.get("ts_code", "X")),
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self._seed_universe(out, ["600000.SH", "600001.SH"])
            manifest_path = out / MANIFEST_FILENAME
            write_manifest(manifest_path, _bm([_result("daily", 1)], ()))
            bytes_before = manifest_path.read_bytes()
            args = [
                "--output-dir", str(out), "--endpoints", "daily",
                "--start-date", "20250101", "--end-date", "20251231",
                "--rate-limit-sleep-ms", "0",
            ]
            with patch("src.data.tushare.fetcher.time.sleep"), \
                    patch.object(mod.TushareClient, "from_environment", return_value=client), \
                    patch.object(mod, "write_manifest", side_effect=OSError("disk full")):
                rc = mod.main(args)
            self.assertEqual(rc, 1)
            self.assertEqual(manifest_path.read_bytes(), bytes_before)

    def test_main_keeps_manifest_on_hard_abort_with_holes(self) -> None:
        # P3-7b red line: a run that records a hole and then hits a hard
        # (non-retryable) abort never reaches the manifest update — and the
        # prior manifest is left byte-for-byte intact. Deleting it (the old
        # behavior) destroyed the hole ledger the guards exist to keep; the
        # exit 1 already stops any orchestrated build (EXIT_FETCH_HARD).
        mod = self._load_cli()

        def side_effect(api, **p):
            if api == "namechange":
                raise TushareClientError("returned None — rate limit exceeded")  # transient → hole
            if api == "suspend_d":
                raise TushareClientError("Tushare suspend_d invalid token / 权限不足")  # hard
            return pd.DataFrame()

        client = MagicMock()
        client.call = MagicMock(side_effect=side_effect)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            manifest_path = out / MANIFEST_FILENAME
            write_manifest(manifest_path, _bm([_result("daily", 1)], ()))
            bytes_before = manifest_path.read_bytes()
            args = [
                "--output-dir", str(out), "--endpoints", "namechange,suspend_d",
                "--rate-limit-sleep-ms", "0",
            ]
            with patch("src.data.tushare.fetcher.time.sleep"), \
                    patch.object(mod.TushareClient, "from_environment", return_value=client):
                rc = mod.main(args)
            self.assertEqual(rc, 1)  # hard abort
            self.assertEqual(manifest_path.read_bytes(), bytes_before)

    def test_main_keeps_manifest_on_hard_abort_without_holes(self) -> None:
        # A hard abort that wrote PARTIAL output (stock_basic writes
        # active_stocks then aborts on the delisted call) also leaves the
        # manifest untouched: it describes the dir as of the LAST completed
        # run; the aborted run's partial progress is re-recorded by the next
        # completed run (refresh/freshness re-attempt what it touched).
        mod = self._load_cli()

        def side_effect(api, **p):
            if api == "stock_basic" and p.get("list_status") == "L":
                return pd.DataFrame({"ts_code": ["600000.SH"]})  # active written
            if api == "stock_basic":
                raise TushareClientError("stock_basic invalid token / 权限不足")  # delisted hard
            return pd.DataFrame()

        client = MagicMock()
        client.call = MagicMock(side_effect=side_effect)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            manifest_path = out / MANIFEST_FILENAME
            write_manifest(manifest_path, _bm([_result("daily", 1)], ()))
            bytes_before = manifest_path.read_bytes()
            args = [
                "--output-dir", str(out), "--endpoints", "stock_basic",
                "--rate-limit-sleep-ms", "0",
            ]
            with patch("src.data.tushare.fetcher.time.sleep"), \
                    patch.object(mod.TushareClient, "from_environment", return_value=client):
                rc = mod.main(args)
            self.assertEqual(rc, 1)  # hard abort
            self.assertTrue((out / "active_stocks.parquet").exists())  # partial output
            self.assertEqual(len(client.call.call_args_list), 2)  # active ok, delisted aborted
            self.assertEqual(manifest_path.read_bytes(), bytes_before)

    def test_main_unreadable_manifest_kept_and_returns_1(self) -> None:
        # P3-7b red line: a corrupt manifest at run start stops the run (exit
        # 1) and is LEFT IN PLACE for inspection — only --reset-manifest
        # removes it.
        mod = self._load_cli()
        client = MagicMock()
        client.call = MagicMock(side_effect=lambda api, **p: pd.DataFrame())
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            manifest_path = out / MANIFEST_FILENAME
            manifest_path.write_text("{ not json", encoding="utf-8")
            bytes_before = manifest_path.read_bytes()
            args = [
                "--output-dir", str(out), "--endpoints", "namechange",
                "--rate-limit-sleep-ms", "0",
            ]
            with patch.object(mod.TushareClient, "from_environment", return_value=client):
                rc = mod.main(args)
            self.assertEqual(rc, 1)
            self.assertEqual(manifest_path.read_bytes(), bytes_before)
            client.call.assert_not_called()  # refused BEFORE any fetching

    def test_main_reset_manifest_clears_and_rebuilds_fresh(self) -> None:
        # --reset-manifest is the ONLY clear path: a corrupt (or any) prior
        # manifest is removed up front and the run records a fresh one.
        mod = self._load_cli()
        client = MagicMock()
        client.call = MagicMock(
            side_effect=lambda api, **p: self._daily_row(p.get("ts_code", "X")),
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self._seed_universe(out, ["600000.SH", "600001.SH"])
            manifest_path = out / MANIFEST_FILENAME
            manifest_path.write_text("{ not json", encoding="utf-8")
            args = [
                "--output-dir", str(out), "--endpoints", "daily",
                "--start-date", "20250101", "--end-date", "20251231",
                "--rate-limit-sleep-ms", "0", "--reset-manifest",
            ]
            with patch("src.data.tushare.fetcher.time.sleep"), \
                    patch.object(mod.TushareClient, "from_environment", return_value=client):
                rc = mod.main(args)
            self.assertEqual(rc, 0)
            fresh = read_manifest(manifest_path)
            assert fresh is not None
            self.assertEqual(fresh.endpoints["daily"].status, "complete")
            self.assertEqual(fresh.endpoints["daily"].coverage_end_date, "20251231")

    def test_main_reset_manifest_oserror_returns_1(self) -> None:
        # If the explicit clear itself fails (read-only dir / lock), main
        # returns 1 cleanly without fetching anything.
        mod = self._load_cli()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            write_manifest(out / MANIFEST_FILENAME, _bm([_result("daily", 1)], ()))
            with patch.object(mod, "clear_manifest", side_effect=OSError("read-only dir")):
                rc = mod.main([
                    "--output-dir", str(out), "--endpoints", "namechange",
                    "--rate-limit-sleep-ms", "0", "--reset-manifest",
                ])
            self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
