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
    _run_date_is_non_trading,
    build_plan,
    run_daily_update,
)

TODAY = date(2026, 6, 10)
STAGES = ("fetch", "registry", "bins", "membership", "universe", "benchmark", "validate")


def _mk_bundle(path: Path, marker: str) -> None:
    # A real qlib bundle is identified by its calendar spine calendars/day.txt — the
    # marker _live_bundle_present (and bundle_manifest._calendar_path) key on. Store the
    # test's identity tag THERE so seeded bundles are structurally real, not bare dirs.
    (path / "calendars").mkdir(parents=True)
    (path / "calendars" / "day.txt").write_text(marker, encoding="utf-8")


def _marker(path: Path) -> str:
    return (path / "calendars" / "day.txt").read_text(encoding="utf-8")


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
    kw.setdefault("now", TODAY)  # overridable (e.g. a weekend date for the gate tests)
    return DailyUpdateConfig(
        tushare_dir=tmp / "raw",
        provider_dir=tmp / "provider",
        delisted_registry=tmp / "raw" / "delisted_registry.parquet",
        reference_cases=tmp / "reference_cases.yaml",
        **kw,
    )


class StartDateDefaultTests(unittest.TestCase):
    """阶段1 footgun fix: the default fetch start must be the 2018+ bundle
    start (20180101), NOT 20000101. The bins build has no range filter, so a
    pre-2018 fetch silently widens the built calendar — the exact contamination
    阶段1 had to quarantine. A naive run must default to the bundle's start."""

    def test_config_default_start_is_bundle_start(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(_config(Path(t)).start_date, "20180101")

    def test_default_plan_fetches_from_2018_not_2000(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            plan = build_plan(_config(Path(t)))
            i = plan.fetch.index("--start-date")
            self.assertEqual(plan.fetch[i + 1], "20180101")
            self.assertNotIn("20000101", plan.fetch)

    def test_cli_argparse_default_start_is_2018(self) -> None:
        from scripts.daily_update import _build_arg_parser
        args = _build_arg_parser().parse_args([
            "--tushare-dir", "x", "--provider-dir", "y",
            "--delisted-registry", "z", "--reference-cases", "w",
        ])
        self.assertEqual(args.start_date, "20180101")


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
            # The canonical SH000300TR is MANDATORY in the orchestrated rebuild (codex
            # P1): an empty best-effort list makes an H00300.CSI entitlement/fetch
            # failure abort the update, not ship a TR-less bundle that fails every run.
            self.assertIn("--best-effort", plan.benchmark)
            self.assertEqual(
                plan.benchmark[plan.benchmark.index("--best-effort") + 1], "",
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


class TradingCalendarGateTests(unittest.TestCase):
    """PR-O calendar gate: a non-trading (weekend) run no-ops with exit 0 and runs
    NO stage; trading-day runs proceed normally (the rest of the suite, now=Wed)."""

    def test_run_date_is_non_trading_pure(self) -> None:
        # Exhaustive over the 7-day cycle: Mon-Fri trade, Sat/Sun do not (weekend gate;
        # weekday holidays handled downstream). 2026-06-08 is a Monday.
        week = {
            date(2026, 6, 8): False, date(2026, 6, 9): False, date(2026, 6, 10): False,
            date(2026, 6, 11): False, date(2026, 6, 12): False,  # Mon-Fri
            date(2026, 6, 13): True, date(2026, 6, 14): True,     # Sat, Sun
        }
        for d, expected in week.items():
            self.assertEqual(expected, _run_date_is_non_trading(d), d.isoformat())

    def test_weekend_run_is_noop_exit_0_and_runs_no_stage(self) -> None:
        # The no-op fires only because a LIVE bundle exists (seeded healthy) — that is
        # the gate's premise: "already current, skip the redundant weekend refresh".
        with tempfile.TemporaryDirectory() as t:
            cfg = _config(Path(t), now=date(2026, 6, 13))  # Saturday
            _mk_bundle(cfg.provider_dir, "OLD")  # healthy live bundle (repair passes)
            rec = _Recorder()
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_OK)
            self.assertEqual(rec.calls, [])  # gate returned before any stage
            self.assertEqual(_marker(cfg.provider_dir), "OLD")  # bundle untouched

    def test_weekend_run_still_repairs_a_crashed_swap_then_noops(self) -> None:
        # codex P1: Stage 0 crash-repair MUST run even on a non-trading day. A Friday
        # swap that crashed mid-rename (live provider missing, .bak + .new present)
        # must be COMPLETED on Saturday, not left broken (no live bundle) all weekend.
        with tempfile.TemporaryDirectory() as t:
            cfg = _config(Path(t), now=date(2026, 6, 13))  # Saturday
            _mk_bundle(bak_dir(cfg.provider_dir), "OLD")
            _mk_bundle(new_dir(cfg.provider_dir), "PRIOR-VALIDATED")
            rec = _Recorder()
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_OK)
            # repair completed the interrupted swap (live provider restored)...
            self.assertEqual(_marker(cfg.provider_dir), "PRIOR-VALIDATED")
            # ...then the gate no-op'd — NO fetch/build/swap stage ran.
            self.assertEqual(rec.calls, [])

    def test_weekend_restored_from_backup_then_noops(self) -> None:
        # Self-review coverage: the 'restored-from-backup' repair branch (only .bak on
        # disk) also feeds the gate's live-bundle check. Repair restores .bak -> the live
        # provider, then the gate no-ops — a present, validated, one-day-old bundle is
        # exactly the "skip the redundant weekend refresh" case.
        with tempfile.TemporaryDirectory() as t:
            cfg = _config(Path(t), now=date(2026, 6, 13))  # Saturday
            _mk_bundle(bak_dir(cfg.provider_dir), "BACKUP")  # only .bak (live + .new gone)
            rec = _Recorder()
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_OK)
            self.assertEqual(rec.calls, [])  # gate no-op'd after repair restored the bundle
            self.assertEqual(_marker(cfg.provider_dir), "BACKUP")  # restored from backup

    def test_explicit_end_date_overrides_the_weekend_gate(self) -> None:
        # codex P2: an explicit --end-date is a deliberate backfill / catch-up (recover a
        # missed Friday update on Saturday) and MUST run the FULL pipeline to a live swap,
        # never silently no-op. Assert the whole chain ran, not just that fetch started.
        with tempfile.TemporaryDirectory() as t:
            cfg = _config(Path(t), now=date(2026, 6, 13), end_date="20260612")  # Sat -> Fri
            _mk_bundle(cfg.provider_dir, "OLD")  # healthy live bundle
            _write_snapshot(cfg.tushare_dir, "20260613")  # stamped to the frozen run_date
            rec = _Recorder(staging=new_dir(cfg.provider_dir))
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_OK)
            self.assertEqual(list(STAGES), rec.calls)  # full backfill ran to the swap...
            self.assertEqual(_marker(cfg.provider_dir), "NEW")  # ...despite Saturday
            self.assertEqual(_marker(bak_dir(cfg.provider_dir)), "OLD")

    def test_weekend_with_no_live_bundle_bootstraps_to_a_live_swap(self) -> None:
        # codex P1 (#2): on a fresh machine (no provider / no .bak / no .new),
        # check_and_repair returns "healthy" but creates NO live bundle. The gate must
        # fall through and run the FULL pipeline to a successful bootstrap swap — never a
        # green-but-empty no-op. (Asserting the live swap, not just that fetch ran.)
        with tempfile.TemporaryDirectory() as t:
            cfg = _config(Path(t), now=date(2026, 6, 13))  # Saturday, nothing on disk
            _write_snapshot(cfg.tushare_dir, "20260613")
            rec = _Recorder(staging=new_dir(cfg.provider_dir))
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_OK)
            self.assertEqual(list(STAGES), rec.calls)  # bootstrap ran, not a no-op
            self.assertEqual(_marker(cfg.provider_dir), "NEW")  # bundle now live

    def test_weekend_with_only_stale_new_bootstraps_to_a_live_swap(self) -> None:
        # codex P1 (#2): a first-ever build died leaving only <provider>.new — repair
        # removes the unprovable .new, leaving NO live bundle. The gate falls through and
        # bootstraps a FRESH bundle to a live swap (the orphan .new is discarded).
        with tempfile.TemporaryDirectory() as t:
            cfg = _config(Path(t), now=date(2026, 6, 13))  # Saturday
            _mk_bundle(new_dir(cfg.provider_dir), "ORPHAN-NEW")  # only .new, no live
            _write_snapshot(cfg.tushare_dir, "20260613")
            rec = _Recorder(staging=new_dir(cfg.provider_dir))
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_OK)
            self.assertEqual(list(STAGES), rec.calls)
            self.assertEqual(_marker(cfg.provider_dir), "NEW")  # rebuilt, not the orphan

    def test_weekend_empty_provider_dir_bootstraps_not_noop(self) -> None:
        # Self-review P1: provider_dir EXISTS but is empty (operator mkdir, or an
        # AV/cloud-sync tool wiped a corrupted bundle's files but left the folder). A
        # bare .exists() would no-op into a bundleless success; _live_bundle_present
        # requires the calendar spine, so the gate bootstraps over the empty dir instead.
        with tempfile.TemporaryDirectory() as t:
            cfg = _config(Path(t), now=date(2026, 6, 13))  # Saturday
            cfg.provider_dir.mkdir(parents=True)  # empty dir — exists() True, no bundle
            _write_snapshot(cfg.tushare_dir, "20260613")
            rec = _Recorder(staging=new_dir(cfg.provider_dir))
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_OK)
            self.assertEqual(list(STAGES), rec.calls)  # bootstrapped, not no-op'd
            self.assertEqual(_marker(cfg.provider_dir), "NEW")  # swapped over the empty dir

    def test_weekend_garbage_provider_dir_bootstraps_not_noop(self) -> None:
        # codex P1: a NON-EMPTY but non-bundle provider_dir (a stray file / half-copied
        # garbage layout, no calendars/day.txt) must NOT be trusted as a live bundle —
        # "non-empty" admits garbage. The gate keys on the qlib calendar spine, so it
        # bootstraps a real bundle over the garbage instead of no-op'ing on it.
        with tempfile.TemporaryDirectory() as t:
            cfg = _config(Path(t), now=date(2026, 6, 13))  # Saturday
            cfg.provider_dir.mkdir(parents=True)
            (cfg.provider_dir / "stray.txt").write_text("not a bundle", encoding="utf-8")
            _write_snapshot(cfg.tushare_dir, "20260613")
            rec = _Recorder(staging=new_dir(cfg.provider_dir))
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_OK)
            self.assertEqual(list(STAGES), rec.calls)  # bootstrapped, not no-op'd
            self.assertEqual(_marker(cfg.provider_dir), "NEW")  # real bundle over garbage

    def test_dry_run_preview_precedes_the_gate(self) -> None:
        # The gate is placed AFTER the dry-run preview, so --dry-run still returns
        # EXIT_OK without running stages (and the gate does not pre-empt it).
        with tempfile.TemporaryDirectory() as t:
            cfg = _config(Path(t), now=date(2026, 6, 13), dry_run=True)
            rec = _Recorder()
            rc = run_daily_update(cfg, rec.all())
            self.assertEqual(rc, EXIT_OK)
            self.assertEqual(rec.calls, [])


if __name__ == "__main__":
    unittest.main()
