"""Orchestrator red-line tests (P3-6a). All fake runners + temp dirs — no real
fetch, no real qlib build, no real paths."""

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data_pipeline.bundle_swap import bak_dir, new_dir  # noqa: E402
from src.data_pipeline.daily_update import (  # noqa: E402
    EXIT_FETCH_HARD,
    EXIT_FETCH_HOLES,
    EXIT_OK,
    EXIT_REBUILD,
    EXIT_SNAPSHOT_STALE,
    EXIT_VALIDATE,
    DailyUpdateConfig,
    build_plan,
    run_daily_update,
)

TODAY = date(2026, 6, 10)
STAGES = ("fetch", "registry", "bins", "membership", "universe", "benchmark", "validate")


def _mk_bundle(path: Path, marker: str) -> None:
    path.mkdir(parents=True)
    (path / "calendars.txt").write_text(marker, encoding="utf-8")


def _marker(path: Path) -> str:
    return (path / "calendars.txt").read_text(encoding="utf-8")


def _write_snapshot(tushare_dir: Path, snapshot_date: str = "20260610") -> None:
    tushare_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "ts_code": ["000001.SZ"], "name": ["平安银行"],
        "snapshot_date": [snapshot_date],
    }).to_parquet(tushare_dir / "active_stocks.parquet")


class _Recorder:
    """Fake stage runners: record call order, return scripted exit codes, and
    (for the build stage) optionally create the staged bundle like 05 would."""

    def __init__(self, codes: dict[str, int] | None = None,
                 staging: Path | None = None) -> None:
        self.codes = codes or {}
        self.calls: list[str] = []
        self.argv: dict[str, list[str]] = {}
        self._staging = staging

    def runner(self, stage: str):
        def run(argv: list[str]) -> int:
            self.calls.append(stage)
            self.argv[stage] = argv
            if stage == "bins" and self._staging is not None \
                    and self.codes.get(stage, 0) == 0:
                if not self._staging.exists():
                    _mk_bundle(self._staging, "NEW")
            return self.codes.get(stage, 0)
        return run

    def all(self) -> dict:
        return {s: self.runner(s) for s in STAGES}


def _config(tmp: Path, **kw) -> DailyUpdateConfig:
    return DailyUpdateConfig(
        tushare_dir=tmp / "raw",
        provider_dir=tmp / "provider",
        delisted_registry=tmp / "raw" / "delisted_registry.parquet",
        reference_cases=tmp / "reference_cases.yaml",
        now=TODAY,
        **kw,
    )


class HappyPathTests(unittest.TestCase):

    def test_full_run_executes_all_stages_in_order_and_swaps(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            cfg = _config(tmp)
            _write_snapshot(cfg.tushare_dir)
            _mk_bundle(cfg.provider_dir, "OLD")
            rec = _Recorder(staging=new_dir(cfg.provider_dir))
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_OK)
            self.assertEqual(list(STAGES), rec.calls)  # exact order
            self.assertEqual(_marker(cfg.provider_dir), "NEW")   # swapped
            self.assertEqual(_marker(bak_dir(cfg.provider_dir)), "OLD")

    def test_plan_wires_paths_and_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            cfg = _config(Path(t), allow_holey_fetch=True)
            plan = build_plan(cfg)
            self.assertIn("--refresh-current", plan.fetch)
            self.assertIn("20260610", plan.fetch)  # end_date defaulted to now
            # codex P2: the ONE frozen run date is stamped into the fetch, so
            # a run crossing midnight stamps the planned date.
            self.assertIn("--snapshot-date", plan.fetch)
            self.assertEqual(
                plan.fetch[plan.fetch.index("--snapshot-date") + 1], "20260610",
            )
            self.assertIn("--allow-holey-fetch", plan.bins)
            staging = str(new_dir(cfg.provider_dir))
            self.assertIn(staging, plan.bins)
            self.assertIn(staging, plan.membership)
            self.assertIn(staging, plan.universe)
            # 07 benchmark ingest writes into the SAME staging dir (survives swap).
            self.assertIn("--provider-dir", plan.benchmark)
            self.assertEqual(
                plan.benchmark[plan.benchmark.index("--provider-dir") + 1], staging,
            )
            self.assertIn(staging, plan.validate)  # 06 validates the STAGED dir

    def test_fetch_holes_with_override_continues(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            cfg = _config(tmp, allow_holey_fetch=True)
            _write_snapshot(cfg.tushare_dir)
            rec = _Recorder({"fetch": 3}, staging=new_dir(cfg.provider_dir))
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_OK)
            self.assertEqual(list(STAGES), rec.calls)


class ShortCircuitTests(unittest.TestCase):
    """RED LINE: a failing stage stops the run; nothing downstream executes."""

    def test_fetch_hard_failure_stops_everything(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            cfg = _config(Path(t))
            rec = _Recorder({"fetch": 1})
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_FETCH_HARD)
            self.assertEqual(["fetch"], rec.calls)  # nothing after fetch

    def test_fetch_holes_without_override_stops(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            cfg = _config(Path(t))
            rec = _Recorder({"fetch": 3})
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_FETCH_HOLES)
            self.assertEqual(["fetch"], rec.calls)

    def test_rebuild_failure_stops_before_validate(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            cfg = _config(tmp)
            _write_snapshot(cfg.tushare_dir)
            rec = _Recorder({"bins": 1})
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_REBUILD)
            self.assertEqual(["fetch", "registry", "bins"], rec.calls)


class SnapshotStageTests(unittest.TestCase):

    def test_stale_snapshot_refuses_without_override(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            cfg = _config(tmp)
            _write_snapshot(cfg.tushare_dir, "20260601")  # not today
            rec = _Recorder()
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_SNAPSHOT_STALE)
            self.assertEqual(["fetch"], rec.calls)  # stopped at snapshot stage

    def test_missing_snapshot_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            cfg = _config(Path(t))  # no active_stocks.parquet at all
            rec = _Recorder()
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_SNAPSHOT_STALE)

    def test_stale_snapshot_with_holey_override_warns_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            cfg = _config(tmp, allow_holey_fetch=True)
            _write_snapshot(cfg.tushare_dir, "20260601")
            rec = _Recorder(staging=new_dir(cfg.provider_dir))
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_OK)


class ValidateGateTests(unittest.TestCase):
    """RED LINE: a failed validation NEVER swaps; the live bundle is untouched."""

    def test_validate_failure_never_swaps(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            cfg = _config(tmp)
            _write_snapshot(cfg.tushare_dir)
            _mk_bundle(cfg.provider_dir, "OLD")
            # 06 exit >= 2 = a check FAILED (1 is warnings-only = pass).
            rec = _Recorder({"validate": 2}, staging=new_dir(cfg.provider_dir))
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_VALIDATE)
            self.assertEqual(_marker(cfg.provider_dir), "OLD")     # untouched
            self.assertFalse(bak_dir(cfg.provider_dir).exists())   # stage 1 never ran
            self.assertTrue(new_dir(cfg.provider_dir).exists())    # staging left for autopsy

    def test_validate_warnings_only_is_a_pass_and_swaps(self) -> None:
        # codex P1: 06 returns 1 when every check PASSED but warnings exist —
        # routine with reference cases present. The orchestrator must swap, or
        # a valid daily update would wedge permanently on a benign warning.
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            cfg = _config(tmp)
            _write_snapshot(cfg.tushare_dir)
            _mk_bundle(cfg.provider_dir, "OLD")
            rec = _Recorder({"validate": 1}, staging=new_dir(cfg.provider_dir))
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_OK)
            self.assertEqual(_marker(cfg.provider_dir), "NEW")  # swapped
            self.assertEqual(_marker(bak_dir(cfg.provider_dir)), "OLD")


class DryRunTests(unittest.TestCase):
    """RED LINE: --dry-run executes nothing and mutates nothing."""

    def test_dry_run_zero_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            cfg = _config(tmp, dry_run=True)
            # Even a repairable crash state must be REPORTED, not repaired.
            _mk_bundle(bak_dir(cfg.provider_dir), "OLD")
            _mk_bundle(new_dir(cfg.provider_dir), "NEW")
            rec = _Recorder()
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_OK)
            self.assertEqual([], rec.calls)                       # nothing executed
            self.assertFalse(cfg.provider_dir.exists())           # nothing repaired
            self.assertTrue(bak_dir(cfg.provider_dir).exists())
            self.assertTrue(new_dir(cfg.provider_dir).exists())
            self.assertFalse(cfg.tushare_dir.exists())            # nothing written


class StartupRepairTests(unittest.TestCase):

    def test_interrupted_swap_repaired_before_stages(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            cfg = _config(tmp)
            _write_snapshot(cfg.tushare_dir)
            # Mid-swap crash state from a PRIOR run.
            _mk_bundle(bak_dir(cfg.provider_dir), "OLD")
            _mk_bundle(new_dir(cfg.provider_dir), "PRIOR-VALIDATED")
            rec = _Recorder(staging=new_dir(cfg.provider_dir))
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_OK)
            # Repair completed the interrupted swap first; this run then built
            # and swapped its own NEW bundle on top.
            self.assertEqual(_marker(cfg.provider_dir), "NEW")
            self.assertEqual(_marker(bak_dir(cfg.provider_dir)), "PRIOR-VALIDATED")


if __name__ == "__main__":
    unittest.main()
