"""Single-entry daily data update: fetch → snapshot → rebuild → validate → swap
(P3-6a).

One orchestrated run brings the raw tushare dump current, rebuilds the FULL
qlib provider bundle into ``<provider>.new``, validates it, and atomically
swaps it live (``src.data_pipeline.bundle_swap``). Every stage is fail-loud and
short-circuits the rest; each failing stage maps to a DISTINCT exit code so a
scheduler (Phase 4 — out of scope here) can tell where a run died:

    0  success
    2  configuration / setup error
    10 startup repair found an unrepairable bundle state
    11 fetch failed hard (01 exit 1/2)
    12 fetch completed WITH HOLES and --allow-holey-fetch was not given
    13 active-stocks snapshot not refreshed to today (and no override)
    14 rebuild failed (02 registry / 05 bins / 03 membership / 04 universe)
    15 validation failed (06 on the staged bundle)
    16 swap failed

Path flow is END-TO-END EXPLICIT: the orchestrator passes every path to every
numbered script as CLI argv (all six are pure-argparse — verified in Step 0; no
``QUANT_*`` env coupling anywhere in the chain). The numbered scripts are
invoked IN-PROCESS via their ``main(argv) -> int`` entry points (loaded with
importlib because their filenames start with digits); tests inject fake
runners.

Stage notes:
- fetch runs ``01_fetch_tushare --refresh-current`` so the units a daily update
  must bring current (stock_basic, namechange / suspend_d, the final year of
  the per-ticker endpoints) ignore resume's exists-skip.
- the snapshot stage verifies the refresh LANDED: the embedded snapshot_date of
  active_stocks.parquet (P3-5) must equal the run date. With
  ``--allow-holey-fetch`` a stale snapshot only warns (the operator already
  sanctioned partial data, the manifest carries the stock_basic hole, and the
  bundle is stamped built-from-holey-fetch — the recommend gate still refuses
  it by default).
- rebuild order is 02 → 05 → 03 → 04: 05 atomically REPLACES its output dir
  when promoting its staging, so the instruments written by 03 / 04 must land
  AFTER it.
- 06 validates ``<provider>.new`` — never the live bundle — and only a passing
  validation reaches the swap. The live bundle is untouched until the swap's
  first rename.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from src.core.logger import get_logger
from src.data.active_stocks_snapshot import SnapshotDateError, embedded_snapshot_date
from src.data_pipeline.bundle_swap import (
    BundleSwapError,
    check_and_repair,
    new_dir,
    swap,
)

_logger = get_logger(__name__)

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "data_pipeline"

# Exit codes (module-level constants so tests assert symbolically).
EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_UNREPAIRABLE = 10
EXIT_FETCH_HARD = 11
EXIT_FETCH_HOLES = 12
EXIT_SNAPSHOT_STALE = 13
EXIT_REBUILD = 14
EXIT_VALIDATE = 15
EXIT_SWAP = 16

Runner = Callable[[list[str]], int]


class DailyUpdateError(RuntimeError):
    """Configuration / orchestration failure (fail-loud)."""


@dataclass(frozen=True)
class DailyUpdateConfig:
    """Inputs for one daily update run. All paths explicit — no env coupling."""

    tushare_dir: Path
    provider_dir: Path
    delisted_registry: Path
    reference_cases: Path
    start_date: str = "20000101"
    end_date: str | None = None  # None -> today (YYYYMMDD) at run time
    allow_holey_fetch: bool = False
    dry_run: bool = False
    rate_limit_sleep_ms: int | None = None  # None -> 01's own default
    # Injectable "today" (value-injection): drives end_date's default and the
    # snapshot-freshness verification. Production leaves None -> system date.
    now: date | None = None


def _load_script_main(filename: str) -> Runner:
    """Load ``scripts/data_pipeline/<filename>``'s ``main`` via importlib.

    The numbered filenames (``01_…``) are not importable as module names, so
    they are loaded from file location — same approach as the repo's CLI
    integration tests.
    """
    path = _SCRIPTS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise DailyUpdateError(f"Cannot load pipeline script {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    main = getattr(module, "main", None)
    if main is None:
        raise DailyUpdateError(f"{path} has no main(argv) entry point")
    return main  # type: ignore[no-any-return]


def _default_runners() -> dict[str, Runner]:
    """Lazy per-stage loaders so importing this module stays cheap."""
    return {
        "fetch": lambda argv: _load_script_main("01_fetch_tushare.py")(argv),
        "registry": lambda argv: _load_script_main("02_build_delisted_registry.py")(argv),
        "bins": lambda argv: _load_script_main("05_build_qlib_bins.py")(argv),
        "membership": lambda argv: _load_script_main("03_resolve_index_membership.py")(argv),
        "universe": lambda argv: _load_script_main("04_build_universe_files.py")(argv),
        "validate": lambda argv: _load_script_main("06_validate_pit_data.py")(argv),
    }


@dataclass
class DailyUpdatePlan:
    """The per-stage argv this run will execute (also the --dry-run output)."""

    fetch: list[str] = field(default_factory=list)
    registry: list[str] = field(default_factory=list)
    bins: list[str] = field(default_factory=list)
    membership: list[str] = field(default_factory=list)
    universe: list[str] = field(default_factory=list)
    validate: list[str] = field(default_factory=list)


def build_plan(config: DailyUpdateConfig) -> DailyUpdatePlan:
    """Assemble every stage's argv up front (pure; also what --dry-run prints)."""
    end_date = config.end_date or (
        (config.now if config.now is not None else date.today()).strftime("%Y%m%d")
    )
    staging = new_dir(config.provider_dir)
    fetch = [
        "--output-dir", str(config.tushare_dir),
        "--start-date", config.start_date,
        "--end-date", end_date,
        "--refresh-current",
    ]
    if config.rate_limit_sleep_ms is not None:
        fetch += ["--rate-limit-sleep-ms", str(config.rate_limit_sleep_ms)]
    bins = [
        "--tushare-dir", str(config.tushare_dir),
        "--delisted-registry", str(config.delisted_registry),
        "--output-dir", str(staging),
    ]
    if config.allow_holey_fetch:
        bins.append("--allow-holey-fetch")
    return DailyUpdatePlan(
        fetch=fetch,
        registry=[
            "--tushare-dir", str(config.tushare_dir),
            "--reference-cases", str(config.reference_cases),
            "--output", str(config.delisted_registry),
        ],
        bins=bins,
        membership=[
            "--tushare-dir", str(config.tushare_dir),
            "--output-dir", str(staging),
            "--reference-cases", str(config.reference_cases),
        ],
        universe=[
            "--tushare-dir", str(config.tushare_dir),
            "--delisted-registry", str(config.delisted_registry),
            "--output-dir", str(staging),
        ],
        validate=[
            "--provider-dir", str(staging),
            "--delisted-registry", str(config.delisted_registry),
            "--reference-cases", str(config.reference_cases),
        ],
    )


def _verify_snapshot_refreshed(config: DailyUpdateConfig) -> int:
    """The snapshot stage: prove the fetch refreshed active_stocks TODAY.

    Reads the embedded snapshot_date (P3-5). A missing / unreadable / stale
    stamp fails loud (EXIT_SNAPSHOT_STALE) — unless the operator passed
    --allow-holey-fetch, which already sanctions building from partial data;
    then it warns and continues (the fetch manifest carries the stock_basic
    hole, so the bundle is stamped built-from-holey-fetch downstream anyway).
    """
    today = config.now if config.now is not None else date.today()
    path = config.tushare_dir / "active_stocks.parquet"
    try:
        snapshot = embedded_snapshot_date(
            pd.read_parquet(path), source=str(path),
        )
        problem = (
            None if snapshot == today
            else f"embedded snapshot_date {snapshot} != run date {today}"
        )
    except (OSError, ValueError, SnapshotDateError) as exc:
        problem = str(exc)
    if problem is None:
        _logger.info("Snapshot stage OK: active_stocks refreshed for %s.", today)
        return EXIT_OK
    if config.allow_holey_fetch:
        _logger.warning(
            "Snapshot NOT refreshed (%s) — continuing because "
            "--allow-holey-fetch sanctioned partial data; the bundle will be "
            "stamped accordingly.", problem,
        )
        return EXIT_OK
    _logger.error(
        "Snapshot stage FAILED: %s. The fetch did not land a fresh "
        "active_stocks snapshot (was stock_basic holed?). Refusing to rebuild "
        "from a stale ST/name view; pass --allow-holey-fetch to proceed "
        "anyway.", problem,
    )
    return EXIT_SNAPSHOT_STALE


def run_daily_update(
    config: DailyUpdateConfig,
    runners: Mapping[str, Runner] | None = None,
) -> int:
    """Run the full daily update; returns the process exit code.

    ``runners`` overrides the per-stage entry points (tests inject fakes; the
    default loads the real numbered scripts).
    """
    active = dict(_default_runners())
    if runners:
        active.update(runners)
    plan = build_plan(config)

    if config.dry_run:
        _logger.info("[dry-run] daily update plan — nothing will be executed:")
        for stage in ("fetch", "registry", "bins", "membership", "universe", "validate"):
            _logger.info("  [dry-run] %s: %s", stage, " ".join(getattr(plan, stage)))
        state = check_and_repair(config.provider_dir, dry_run=True)
        _logger.info("  [dry-run] startup bundle state: %s", state)
        _logger.info("  [dry-run] swap: %s -> %s", new_dir(config.provider_dir),
                     config.provider_dir)
        return EXIT_OK

    # Stage 0: resolve any crash-interrupted prior swap BEFORE touching data.
    try:
        action = check_and_repair(config.provider_dir)
    except OSError as exc:
        _logger.error("Startup bundle-state repair FAILED: %s", exc)
        return EXIT_UNREPAIRABLE
    if action != "healthy":
        _logger.warning("Startup bundle-state action: %s", action)

    # Stage 1: fetch (01 --refresh-current). Exit 3 = completed-with-holes.
    rc = active["fetch"](plan.fetch)
    if rc == 3 and not config.allow_holey_fetch:
        _logger.error(
            "Fetch completed WITH HOLES (exit 3) and --allow-holey-fetch was "
            "not given. The build gate would refuse this dump; stopping here. "
            "Re-run to self-heal the holes, or pass --allow-holey-fetch to "
            "build a research bundle from partial data."
        )
        return EXIT_FETCH_HOLES
    if rc not in (0, 3):
        _logger.error("Fetch FAILED (exit %d); aborting the update.", rc)
        return EXIT_FETCH_HARD

    # Stage 2: prove the active-stocks snapshot was refreshed today.
    rc = _verify_snapshot_refreshed(config)
    if rc != EXIT_OK:
        return rc

    # Stage 3: full rebuild into <provider>.new (02 -> 05 -> 03 -> 04; 05 must
    # precede 03/04 because its staging-promote REPLACES the output dir).
    for stage in ("registry", "bins", "membership", "universe"):
        rc = active[stage](getattr(plan, stage))
        if rc != 0:
            _logger.error("Rebuild stage %r FAILED (exit %d); the live bundle "
                          "is untouched.", stage, rc)
            return EXIT_REBUILD

    # Stage 4: validate the STAGED bundle. Only a pass reaches the swap.
    rc = active["validate"](plan.validate)
    if rc != 0:
        _logger.error(
            "Validation FAILED (exit %d) on %s; NOT swapping — the live "
            "bundle stays as it was.", rc, new_dir(config.provider_dir),
        )
        return EXIT_VALIDATE

    # Stage 5: atomic two-stage swap.
    try:
        swap(config.provider_dir)
    except (BundleSwapError, OSError) as exc:
        _logger.error("Swap FAILED: %s", exc)
        return EXIT_SWAP
    _logger.info("Daily update complete: %s is live.", config.provider_dir)
    return EXIT_OK
