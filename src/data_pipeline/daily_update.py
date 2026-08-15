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
    17 another run holds the single-flight lock for this provider (CLI; 阶段5 PR-P)

Path flow is END-TO-END EXPLICIT: the orchestrator passes every path to every
numbered script as CLI argv (all six are pure-argparse — verified in Step 0; no
``QUANT_*`` env coupling anywhere in the chain). The numbered scripts are
invoked IN-PROCESS via their ``main(argv) -> int`` entry points (loaded with
importlib because their filenames start with digits); tests inject fake
runners.

Stage notes:
- fetch runs ``01_fetch_tushare --refresh-current`` so the AGGREGATE units a
  daily update must bring current (stock_basic, namechange / suspend_d) ignore
  resume's exists-skip. The per-ticker endpoints (daily / adj_factor /
  daily_basic) are brought current by the P3-7b freshness rule instead: a year
  file is re-pulled exactly when its max(trade_date) stops short of what the
  run's range expects, so a same-day crash re-run skips already-current files.
- the snapshot stage verifies the refresh LANDED: the embedded snapshot_date of
  active_stocks.parquet (P3-5) must equal the run date. With
  ``--allow-holey-fetch`` a stale snapshot only warns (the operator already
  sanctioned partial data, the manifest carries the stock_basic hole, and the
  bundle is stamped built-from-holey-fetch — the recommend gate still refuses
  it by default).
- benchmark ingest (07) runs after 05 against the SAME staging dir, so the CSI
  300 price + total-return index instruments it appends survive the swap (the
  retired xlsx ingest wrote into LIVE and the swap erased them — audit E2).
- rebuild order is 02 → 05 → 03 → 04 → 07: 05 atomically REPLACES its output dir
  when promoting its staging, so the instruments written by 03 / 04 must land
  AFTER it.
- 06 validates ``<provider>.new`` — never the live bundle — and only a passing
  validation reaches the swap. The live bundle is untouched until the swap's
  first rename.
"""

from __future__ import annotations

import importlib.util
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from src.core.logger import get_logger
from src.data.active_stocks_snapshot import SnapshotDateError, embedded_snapshot_date
from src.data_pipeline.bundle_swap import (
    BundleSwapError,
    bak_dir,
    check_and_repair,
    new_dir,
    swap,
)

# The lock NAMING is owned by single_flight; the status guard reads it from
# there rather than restating `<name>.daily_update.lock`, so a rename of the
# convention cannot leave this guard protecting a path nobody locks.
from src.data_pipeline.single_flight import lock_path_for

_logger = get_logger(__name__)

# Operator-facing timestamps use fixed +08:00, mirroring the repo convention
# (src/inference/daily_recommend.py, web/operator_ui/formatting.py). No DST in
# China, so the fixed offset is exact and avoids a tzdata dependency.
_CN_TZ = timezone(timedelta(hours=8))

# Run-status artifact (2026-08-14-daily-update-run-status): one machine-readable
# JSON per run so operators (and the read-only UI) can answer "did last night's
# update succeed, and if not, where did it die" without parsing the rolling log.
# Observability only — never a canonical input; see the proposal's Non-goals.
STATUS_FILENAME = "daily_update_status.json"
# Public so the read-only UI reader can pin its copy against the writer's
# (the two modules deliberately do not import each other).
STATUS_SCHEMA_VERSION = 1

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
EXIT_ALREADY_RUNNING = 17  # CLI single-flight (阶段5 PR-P); see scripts/daily_update.py

Runner = Callable[[list[str]], int]


class DailyUpdateError(RuntimeError):
    """Configuration / orchestration failure (fail-loud)."""


def default_status_path(provider_dir: Path) -> Path:
    """The run-status artifact's default location: ``<provider>.<FILENAME>``.

    A SIBLING of the provider dir, so it survives the atomic swap (which only
    renames the provider dir itself), and NAME-DERIVED so it is unique per
    provider. The first cut used ``<provider>.parent/<FILENAME>``, which
    collapses for sibling bundles — this repo ships exactly that layout
    (``D:/qlib_data/my_cn_data_pit`` and ``…_2015`` both resolved to
    ``D:/qlib_data/daily_update_status.json``), so inspecting the research
    bundle would have shown the PRODUCTION run's status as if it were its own
    (codex #434 r4). Same convention as ``single_flight.lock_path_for``."""
    return provider_dir.with_name(f"{provider_dir.name}.{STATUS_FILENAME}")


def _write_status(path: Path, payload: Mapping[str, object]) -> None:
    """Atomic status write (temp + rename): a killed process must never leave
    a half-written JSON that the UI would read as corrupt."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _record_status(path: Path, payload: Mapping[str, object]) -> None:
    """Best-effort status write: an observability failure SHALL NOT change the
    run's exit code (reverse coupling). Logged ERROR so the gap is visible."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_status(path, payload)
    except OSError as exc:
        _logger.error(
            "run-status artifact write FAILED (%s): %s — the run's exit code "
            "is unaffected; the UI will keep showing the previous record.",
            path, exc,
        )


def _norm(path: Path) -> str:
    """Case/separator-normalized absolute form for overlap comparison:
    ``resolve()`` defeats ``..`` and ``normcase`` matches the Windows
    case-insensitive filesystem semantics."""
    return os.path.normcase(str(path.resolve()))


def _path_within(target: str, root: str) -> bool:
    """Is ``target`` the same as, or inside, ``root``?

    Component-aware, NOT ``startswith(root + os.sep)``: when ``root`` is a
    filesystem ROOT — a plausible layout for a dedicated data drive — that
    spelling builds ``D:\\\\`` (or ``//`` on POSIX) and a genuine child like
    ``D:\\active_stocks.parquet`` fails to match, so the guard would ACCEPT a
    status path inside the canonical tree and the first atomic replace could
    clobber a raw/provider file (codex #434 r3).

    ``commonpath`` raises for inputs on different drives / mixed
    absolute-relative; both inputs here are already ``_norm``-ed absolutes, and
    a cross-drive pair simply is not contained.
    """
    if target == root:
        return True
    try:
        return os.path.commonpath([target, root]) == root
    except ValueError:      # different drives, or no common prefix at all
        return False


@dataclass(frozen=True)
class DailyUpdateConfig:
    """Inputs for one daily update run. All paths explicit — no env coupling."""

    tushare_dir: Path
    provider_dir: Path
    delisted_registry: Path
    reference_cases: Path
    # 2018-01-01: the bundle is a 2018+ point-in-time bundle by design (see
    # config_walk.yaml overall_start). The bins build has NO range filter — it
    # ingests EVERY year present under <tushare-dir>/daily/ — so fetching
    # pre-2018 years here silently widens the built calendar and reintroduces
    # the very contamination 阶段1 had to quarantine. Default to the bundle's
    # start; an operator who genuinely wants full history must opt in explicitly.
    start_date: str = "20180101"
    end_date: str | None = None  # None -> today (YYYYMMDD) at run time
    allow_holey_fetch: bool = False
    dry_run: bool = False
    rate_limit_sleep_ms: int | None = None  # None -> 01's own default
    # Run-status artifact location. None -> default_status_path(provider_dir)
    # (sibling of the provider dir, surviving the swap). Explicit override via
    # the CLI's --status-path keeps the chain's "paths explicit" discipline.
    # Must NOT overlap any canonical input — see __post_init__ (codex P1).
    status_path: Path | None = None
    # Injectable "today" (value-injection): drives end_date's default and the
    # snapshot-freshness verification. Production leaves None -> system date.
    now: date | None = None

    def __post_init__(self) -> None:
        # codex P1: the status write is an UNCONDITIONAL atomic replace — an
        # operator-typo'd --status-path aliasing a canonical input (the live
        # provider tree, the raw tushare tree, the delisted registry, the
        # reference cases) would clobber that input with status JSON before
        # orchestration starts, inverting the guarantee that observability can
        # never affect canonical behavior. Reject overlaps at construction:
        # the CLI maps ValueError to the config-error exit 2 BEFORE any write,
        # consistent with "config errors never write the artifact".
        target = _norm(self.status_path or default_status_path(self.provider_dir))
        for label, root in (
            ("--provider-dir", self.provider_dir),
            ("--tushare-dir", self.tushare_dir),
            # codex P1 round 2: the swap machinery's mandatory staging /
            # rollback siblings are operational paths too — a status file at
            # <provider>.new / <provider>.bak is rmtree'd by check_and_repair
            # / swap, killing the run with exit 10/16.
            ("<provider>.new", new_dir(self.provider_dir)),
            ("<provider>.bak", bak_dir(self.provider_dir)),
        ):
            if _path_within(target, _norm(root)):
                raise ValueError(
                    f"--status-path resolves inside {label} ({root}) — the "
                    f"status artifact's atomic replace would clobber canonical "
                    f"data; refusing (observability must never affect data)"
                )
        # codex P1 round 3: the single-flight LOCK files are siblings of the
        # resources, so the tree checks above cannot see them. Replacing a lock
        # is worse than clobbering data: on POSIX the atomic replace swaps the
        # directory entry while the old inode stays locked, so a second run
        # opens the NEW file, takes a different lock, and proceeds CONCURRENTLY
        # against the same provider — observability would have disabled
        # single-flight. Forbid every lock `scripts/daily_update.py` takes.
        for label, f in (
            ("--delisted-registry", self.delisted_registry),
            ("--reference-cases", self.reference_cases),
            *(("单飞锁", lock_path_for(Path(os.path.abspath(r))))
              for r in (self.provider_dir, self.tushare_dir,
                        self.delisted_registry)),
        ):
            if target == _norm(f):
                raise ValueError(
                    f"--status-path aliases {label} ({f}) — the status "
                    f"artifact's atomic replace would clobber a canonical "
                    f"input; refusing"
                )
        # codex P2: a name-less path (".", a filesystem root) makes
        # _write_status's path.with_name() raise ValueError — which
        # _record_status does not catch (OSError only), so the mistake would
        # abort the run instead of surfacing as a config error. Reject here.
        if self.status_path is not None and not self.status_path.name:
            raise ValueError(
                f"--status-path {self.status_path!r} has no file name — the "
                f"status artifact must be a file path, not a directory/root"
            )


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
        "benchmark": lambda argv: _load_script_main("07_ingest_benchmark.py")(argv),
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
    benchmark: list[str] = field(default_factory=list)
    validate: list[str] = field(default_factory=list)


def build_plan(
    config: DailyUpdateConfig, *, run_date: date | None = None,
) -> DailyUpdatePlan:
    """Assemble every stage's argv up front (pure; also what --dry-run prints).

    ``run_date`` is the ONE frozen date of this run (codex P2): it drives the
    default fetch end_date AND the stock_basic snapshot stamp
    (``--snapshot-date``), so a fetch spanning midnight stamps the planned
    date — the same date the snapshot stage later verifies — instead of
    whatever the wall clock says when the write finally happens.
    """
    if run_date is None:
        run_date = config.now if config.now is not None else date.today()
    end_date = config.end_date or run_date.strftime("%Y%m%d")
    staging = new_dir(config.provider_dir)
    fetch = [
        "--output-dir", str(config.tushare_dir),
        "--start-date", config.start_date,
        "--end-date", end_date,
        "--refresh-current",
        "--snapshot-date", run_date.strftime("%Y%m%d"),
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
        benchmark=[
            "--provider-dir", str(staging),
            "--start-date", config.start_date,
            "--end-date", end_date,
            # SH000300TR (tushare H00300.CSI) is the CANONICAL benchmark (PR-2), so the
            # orchestrated rebuild makes it MANDATORY: an empty best-effort list means a
            # fetch/entitlement failure ABORTS the update loudly instead of shipping a
            # TR-less bundle that would fail every default-config run at backtest time
            # (codex P1). The 07_ingest_benchmark CLI default keeps H00300.CSI best-effort
            # for manual/standalone runs; only the orchestrated daily swap forces it.
            "--best-effort", "",
        ],
        validate=[
            "--provider-dir", str(staging),
            "--delisted-registry", str(config.delisted_registry),
            "--reference-cases", str(config.reference_cases),
        ],
    )


def _verify_snapshot_refreshed(config: DailyUpdateConfig, run_date: date) -> int:
    """The snapshot stage: prove the fetch refreshed active_stocks for THIS run.

    Reads the embedded snapshot_date (P3-5) and compares it against the ONE
    frozen ``run_date`` (the same value the fetch stamped via --snapshot-date —
    codex P2: recomputing "today" here after an hours-long fetch would fail a
    run that crossed midnight even though it refreshed for the planned date).
    A missing / unreadable / mismatched stamp fails loud (EXIT_SNAPSHOT_STALE)
    — unless the operator passed --allow-holey-fetch, which already sanctions
    building from partial data; then it warns and continues (the fetch
    manifest carries the stock_basic hole, so the bundle is stamped
    built-from-holey-fetch downstream anyway).
    """
    path = config.tushare_dir / "active_stocks.parquet"
    try:
        snapshot = embedded_snapshot_date(
            pd.read_parquet(path), source=str(path),
        )
        problem = (
            None if snapshot == run_date
            else f"embedded snapshot_date {snapshot} != run date {run_date}"
        )
    except (OSError, ValueError, SnapshotDateError) as exc:
        problem = str(exc)
    if problem is None:
        _logger.info("Snapshot stage OK: active_stocks refreshed for %s.", run_date)
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


def _run_date_is_non_trading(run_date: date) -> bool:
    """True if ``run_date`` is a NON-trading day for A-shares.

    Currently a WEEKEND check (Sat/Sun) — offline + deterministic, so the
    orchestrator hot path and the tests take NO network (the "no real fetch in dev"
    red line). A-share weekday HOLIDAYS (~10/yr) are intentionally NOT skipped here:
    they fall through to the normal run, whose fetch/freshness gates already no-op
    gracefully on a day with no new bar (the PR #270/#271 holiday-aware floor), so a
    weekday holiday is handled, never WRONGLY skipped. Full holiday-awareness via the
    SSE exchange calendar (tushare ``trade_cal``) is a deliberate follow-up — it would
    add a network call to this gate. Pure -> unit-testable.
    """
    return run_date.weekday() >= 5  # 5 = Saturday, 6 = Sunday


def _live_bundle_present(provider_dir: Path) -> bool:
    """True iff ``provider_dir`` holds a readable qlib bundle skeleton.

    The weekend no-op's premise is "a bundle is already present — skip the redundant
    refresh". Weaker checks are not enough: ``Path.exists()`` (a bare path), a non-empty
    dir, or even the calendar spine ALONE would all pass for an operator ``mkdir``, an
    AV / cloud-sync tool that left the folder after deleting a corrupted bundle's files, a
    stray file, or a partial copy that kept ``calendars/day.txt`` but lost
    ``instruments/all.txt`` / ``features/`` — while readers have NO usable bundle. That is
    the green-but-empty success this guard exists to prevent (codex).

    Require the SAME cheap structural skeleton ``pit_validator._sanity_check_provider``
    uses to define a readable provider — ``calendars/day.txt`` + ``instruments/all.txt`` +
    ``features/`` all present. This stays a cheap, OFFLINE presence check (no qlib init,
    no content validation — deep validity, e.g. a non-empty calendar or real features,
    is 06's / the recommend integrity gate's job). A missing path, a file, an empty dir,
    or a PARTIAL bundle all read as "no live bundle" -> the gate falls through to the
    bootstrap / fail-loud pipeline.
    """
    return (
        (provider_dir / "calendars" / "day.txt").exists()
        and (provider_dir / "instruments" / "all.txt").exists()
        and (provider_dir / "features").exists()
    )


def run_daily_update(
    config: DailyUpdateConfig,
    runners: Mapping[str, Runner] | None = None,
) -> int:
    """Run the full daily update; returns the process exit code.

    Also persists the run-status artifact (schema v1, see STATUS_FILENAME):
    ``state="running"`` at start, ``state="finished"`` + exit_code +
    failed_stage at every terminal state. A ``--dry-run`` mutates nothing —
    including this artifact — so it returns before any status write.
    """
    if config.dry_run:
        return _execute_daily_update(config, runners)[0]
    status_path = config.status_path or default_status_path(config.provider_dir)
    run_date = config.now if config.now is not None else date.today()
    started_at = datetime.now(tz=_CN_TZ)
    base = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "run_date": run_date.isoformat(),
        "started_at": started_at.isoformat(),
    }
    _record_status(status_path, {**base, "state": "running"})
    exit_code, failed_stage, detail = _execute_daily_update(config, runners)
    _record_status(status_path, {
        **base,
        "state": "finished",
        "finished_at": datetime.now(tz=_CN_TZ).isoformat(),
        "exit_code": exit_code,
        "failed_stage": failed_stage,
        "detail": detail,
    })
    return exit_code


def _execute_daily_update(
    config: DailyUpdateConfig,
    runners: Mapping[str, Runner] | None = None,
) -> tuple[int, str | None, str]:
    """The orchestration body; returns ``(exit_code, failed_stage, detail)``.

    ``runners`` overrides the per-stage entry points (tests inject fakes; the
    default loads the real numbered scripts). ``failed_stage`` is the stage key
    (``fetch`` / ``snapshot`` / ``registry`` / … / ``validate`` / ``swap`` /
    ``startup_repair``), ``None`` on success; ``detail`` is a one-line
    human-readable summary for the status artifact.
    """
    active = dict(_default_runners())
    if runners:
        active.update(runners)
    # Freeze the ONE run date up front (codex P2): the fetch stamp, the default
    # end_date, and the snapshot verification all use THIS value, so an
    # hours-long fetch crossing midnight cannot fail its own snapshot check.
    run_date = config.now if config.now is not None else date.today()
    plan = build_plan(config, run_date=run_date)

    if config.dry_run:
        _logger.info("[dry-run] daily update plan — nothing will be executed:")
        for stage in ("fetch", "registry", "bins", "membership", "universe",
                      "benchmark", "validate"):
            _logger.info("  [dry-run] %s: %s", stage, " ".join(getattr(plan, stage)))
        state = check_and_repair(config.provider_dir, dry_run=True)
        _logger.info("  [dry-run] startup bundle state: %s", state)
        _logger.info("  [dry-run] swap: %s -> %s", new_dir(config.provider_dir),
                     config.provider_dir)
        return EXIT_OK, None, "dry-run (nothing executed)"

    # Stage 0: resolve any crash-interrupted prior swap BEFORE the calendar gate. A
    # Friday swap that crashed mid-rename leaves the LIVE provider missing; repair either
    # COMPLETES the interrupted swap (.bak + .new present) or RESTORES the prior bundle
    # from .bak (after a restore the weekend no-op intentionally serves that one-day-old
    # generation until the next trading-day rebuild). Either way it must run even on a
    # closed day — skipping it on a weekend (codex P1) would strand readers with no live
    # bundle until the next trading day.
    # Concurrency: this presumes single-flight execution. swap() is crash-atomic but not
    # reader/run-concurrent (see bundle_swap.swap docstring) — the PR-P scheduler MUST
    # serialize firings, else the gate's live-bundle probe below could observe the brief
    # inter-rename window of a concurrent run. Mutual exclusion is the scheduler's job.
    try:
        action = check_and_repair(config.provider_dir)
    except OSError as exc:
        _logger.error("Startup bundle-state repair FAILED: %s", exc)
        return EXIT_UNREPAIRABLE, "startup_repair", (
            f"startup bundle-state repair failed: {exc}")
    if action != "healthy":
        _logger.warning("Startup bundle-state action: %s", action)

    # Trading-calendar gate (PR-O): no-op with a clean exit 0 on a non-trading day, so
    # a scheduled (PR-P) daily run does not run the full fetch/build/swap pipeline (or
    # churn the bundle) on a closed day — but ONLY when ALL of:
    #   (a) it is a default "today" run. An explicit --end-date (``config.end_date``) is
    #       a deliberate backfill / catch-up (recovering a missed Friday update on a
    #       Saturday) and MUST run, never silently no-op (codex P2);
    #   (b) a usable LIVE bundle actually exists after the Stage 0 repair. The no-op's
    #       premise is "the bundle is already current, skip the redundant refresh" — that
    #       only holds if there is a bundle. On a fresh machine, after a first-ever build
    #       crashed leaving only ``.new`` (which repair just cleared), or when the
    #       provider path exists but is empty / not a real bundle, no usable live provider
    #       exists; a weekend no-op there would report SUCCESS with nothing for readers
    #       (codex P1). ``_live_bundle_present`` requires the readable qlib bundle
    #       skeleton (``calendars/day.txt`` + ``instruments/all.txt`` + ``features/``, per
    #       ``pit_validator._sanity_check_provider``), NOT a bare ``.exists()`` / non-empty
    #       dir / calendar-spine-only — so an empty, garbage, OR partially-copied bundle
    #       all read as absent. Instead fall through to the normal pipeline so it
    #       BOOTSTRAPS a bundle from history (or fails loud with a distinct exit code) —
    #       not a green-but-empty exit.
    if config.end_date is None and _run_date_is_non_trading(run_date):
        if _live_bundle_present(config.provider_dir):
            _logger.info(
                "daily_update: %s is a non-trading day (weekend) — no-op, exit 0 "
                "(calendar gate; pass --end-date to force a backfill/catch-up).",
                run_date.isoformat(),
            )
            return EXIT_OK, None, (
                f"non-trading-day calendar gate no-op ({run_date.isoformat()})")
        _logger.warning(
            "daily_update: %s is a non-trading day but NO usable live bundle exists at "
            "%s — skipping the weekend no-op and running the full pipeline to BOOTSTRAP "
            "a bundle (a no-op here would report success with nothing for readers). If "
            "this dead-ends on a holiday-bridged weekend with the trade calendar "
            "unavailable, re-run on the next trading day or pass an explicit --end-date "
            "set to the last trading day.",
            run_date.isoformat(), config.provider_dir,
        )

    # Stage 1: fetch (01 --refresh-current). Exit 3 = completed-with-holes.
    rc = active["fetch"](plan.fetch)
    if rc == 3 and not config.allow_holey_fetch:
        _logger.error(
            "Fetch completed WITH HOLES (exit 3) and --allow-holey-fetch was "
            "not given. The build gate would refuse this dump; stopping here. "
            "Re-run to self-heal the holes, or pass --allow-holey-fetch to "
            "build a research bundle from partial data."
        )
        return EXIT_FETCH_HOLES, "fetch", (
            "fetch completed with holes; --allow-holey-fetch not given")
    if rc not in (0, 3):
        _logger.error("Fetch FAILED (exit %d); aborting the update.", rc)
        return EXIT_FETCH_HARD, "fetch", f"fetch failed hard (exit {rc})"

    # Stage 2: prove the active-stocks snapshot was refreshed today.
    rc = _verify_snapshot_refreshed(config, run_date)
    if rc != EXIT_OK:
        return rc, "snapshot", (
            "active-stocks snapshot not refreshed to the run date")

    # Stage 3: full rebuild into <provider>.new (02 -> 05 -> 03 -> 04 -> 07;
    # 05 must precede 03/04/07 because its staging-promote REPLACES the output
    # dir, and 07 (benchmark ingest) appends to the all.txt + features that 05
    # writes, so the atomic swap preserves the benchmark instruments (the
    # retired xlsx ingest wrote into LIVE and the swap erased them — audit E2).
    for stage in ("registry", "bins", "membership", "universe", "benchmark"):
        rc = active[stage](getattr(plan, stage))
        if rc != 0:
            _logger.error("Rebuild stage %r FAILED (exit %d); the live bundle "
                          "is untouched.", stage, rc)
            return EXIT_REBUILD, stage, (
                f"rebuild stage {stage!r} failed (exit {rc})")

    # Stage 4: validate the STAGED bundle. Only a pass reaches the swap.
    # 06's exit convention: 0 = clean, 1 = WARNINGS ONLY (every check passed —
    # routine when reference cases are present, e.g. the index-membership
    # check), >= 2 = a check FAILED. Warnings-only is a pass here (codex P1):
    # refusing to swap a valid bundle over a routine warning would wedge the
    # daily update permanently.
    rc = active["validate"](plan.validate)
    if rc == 1:
        _logger.warning(
            "Validation passed WITH WARNINGS (exit 1) on %s — swapping; "
            "review the validator output.", new_dir(config.provider_dir),
        )
    elif rc != 0:
        _logger.error(
            "Validation FAILED (exit %d) on %s; NOT swapping — the live "
            "bundle stays as it was.", rc, new_dir(config.provider_dir),
        )
        return EXIT_VALIDATE, "validate", (
            f"validation failed (exit {rc}) on the staged bundle; not swapping")

    # Stage 5: atomic two-stage swap.
    try:
        swap(config.provider_dir)
    except (BundleSwapError, OSError) as exc:
        _logger.error("Swap FAILED: %s", exc)
        return EXIT_SWAP, "swap", f"swap failed: {exc}"
    _logger.info("Daily update complete: %s is live.", config.provider_dir)
    return EXIT_OK, None, f"daily update complete; {config.provider_dir} is live"
