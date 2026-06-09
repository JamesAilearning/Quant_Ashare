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


class BuildAndWriteTests(unittest.TestCase):

    def test_build_fields_and_injected_timestamp(self) -> None:
        results = [_result("daily", 100), _result("namechange", 1)]
        holes = (_hole("daily", "ts_code=600001.SH year=2020", attempts=5),)
        m = build_manifest(results, holes, "20251231", now=FIXED_NOW)

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
            m = build_manifest([_result("daily", 7)], holes, "20251231", now=FIXED_NOW)
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
        m = build_manifest([_result("daily", 1)], (), "20251231")
        self.assertTrue(m.fetched_at)
        self.assertNotEqual(m.fetched_at, FIXED_NOW.isoformat())


class AtomicWriteTests(unittest.TestCase):

    def test_failed_replace_leaves_prev_manifest_intact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / MANIFEST_FILENAME
            old = build_manifest([_result("daily", 1)], (), "20231231", now=FIXED_NOW)
            write_manifest(path, old)  # establish the prior value

            new = build_manifest(
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
                path, build_manifest([_result("daily", 1)], (), "20251231", now=FIXED_NOW),
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


class MergeTests(unittest.TestCase):

    def test_prev_none_returns_current(self) -> None:
        cur = build_manifest([_result("daily", 1)], (), "20251231", now=FIXED_NOW)
        self.assertIs(merge_manifest(None, cur), cur)

    def test_self_healed_hole_is_dropped(self) -> None:
        # prev holed X@2020; this run ran daily and did NOT hole X@2020 → healed.
        prev = build_manifest(
            [_result("daily", 1)], (_hole("daily", "ts_code=X year=2020"),),
            "20251231", now=FIXED_NOW,
        )
        cur = build_manifest([_result("daily", 2)], (), "20251231", now=FIXED_NOW)
        merged = merge_manifest(prev, cur)
        self.assertEqual(merged.endpoints["daily"].holes, ())
        self.assertEqual(merged.endpoints["daily"].status, "complete")

    def test_unhealed_hole_stays_with_attempts_accumulated(self) -> None:
        # prev holed X@2020 (5 attempts); this run holes X@2020 again (5 attempts).
        prev = build_manifest(
            [_result("daily", 0)], (_hole("daily", "ts_code=X year=2020", attempts=5),),
            "20251231", now=FIXED_NOW,
        )
        cur = build_manifest(
            [_result("daily", 0)], (_hole("daily", "ts_code=X year=2020", attempts=5),),
            "20251231", now=FIXED_NOW,
        )
        merged = merge_manifest(prev, cur)
        kept = merged.endpoints["daily"].holes
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].unit, "ts_code=X year=2020")
        self.assertEqual(kept[0].attempts, 10)  # 5 + 5 accumulated across runs

    def test_coverage_end_date_advances_and_never_regresses(self) -> None:
        prev = build_manifest([_result("daily", 1)], (), "20231231", now=FIXED_NOW)
        cur = build_manifest([_result("daily", 1)], (), "20251231", now=FIXED_NOW)
        self.assertEqual(merge_manifest(prev, cur).endpoints["daily"].coverage_end_date, "20251231")
        # an older current does not pull coverage backwards
        self.assertEqual(merge_manifest(cur, prev).endpoints["daily"].coverage_end_date, "20251231")

    # ---- merge-precision RED LINE: two independent counter-examples ----

    def test_counterexample_does_not_wrongly_remove_unhealed_other_endpoint(self) -> None:
        # "误删 = silent partial": prev has holes in BOTH suspend_d and daily.
        # This run ran ONLY daily (and healed it). suspend_d did NOT run, so its
        # hole MUST be preserved — dropping it would silently mark an incomplete
        # endpoint complete.
        prev = build_manifest(
            [_result("daily", 0), _result("suspend_d", 0)],
            (
                _hole("daily", "ts_code=X year=2020"),
                _hole("suspend_d", "range=20000101-20251231"),
            ),
            "20251231", now=FIXED_NOW,
        )
        cur = build_manifest([_result("daily", 5)], (), "20251231", now=FIXED_NOW)
        merged = merge_manifest(prev, cur)

        self.assertEqual(merged.endpoints["daily"].holes, ())  # daily healed
        # suspend_d untouched — never silently removed for an endpoint that did not run
        self.assertIn("suspend_d", merged.endpoints)
        self.assertEqual(len(merged.endpoints["suspend_d"].holes), 1)
        self.assertEqual(
            merged.endpoints["suspend_d"].holes[0].unit, "range=20000101-20251231",
        )
        self.assertEqual(merged.endpoints["suspend_d"].status, "holes")

    def test_counterexample_healed_hole_does_not_linger(self) -> None:
        # "赖着 = false alarm": prev holed X@2020 AND Y@2021 in daily. This run
        # heals X@2020 but Y@2021 still holes. Merge must DROP X@2020 (gone) and
        # KEEP Y@2021 — precise per-unit, not all-or-nothing in either direction.
        prev = build_manifest(
            [_result("daily", 0)],
            (
                _hole("daily", "ts_code=X year=2020"),
                _hole("daily", "ts_code=Y year=2021"),
            ),
            "20251231", now=FIXED_NOW,
        )
        cur = build_manifest(
            [_result("daily", 1)],
            (_hole("daily", "ts_code=Y year=2021"),),  # only Y still holes; X healed
            "20251231", now=FIXED_NOW,
        )
        merged = merge_manifest(prev, cur)

        units = {h.unit for h in merged.endpoints["daily"].holes}
        self.assertEqual(units, {"ts_code=Y year=2021"})  # X gone (not lingering), Y kept


class ClearTests(unittest.TestCase):

    def test_clear_removes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / MANIFEST_FILENAME
            write_manifest(
                path, build_manifest([_result("daily", 1)], (), "20251231", now=FIXED_NOW),
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
        return pd.DataFrame({
            "ts_code": [ticker], "trade_date": ["20250102"],
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


if __name__ == "__main__":
    unittest.main()
