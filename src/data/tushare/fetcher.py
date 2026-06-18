"""Fetch raw Tushare data for the A-share survivorship-correction pipeline.

Pipeline
--------
::

    Tushare APIs           ->  raw parquet dumps on disk
      stock_basic          ->  active_stocks.parquet / delisted_stocks.parquet
      namechange           ->  all_namechanges.parquet
      suspend_d            ->  suspend_d.parquet
      index_weight         ->  index_weight/{index_code}.parquet
      daily                ->  daily/{year}/{ticker}.parquet
      adj_factor           ->  adj_factor/{year}/{ticker}.parquet
      daily_basic          ->  daily_basic/{year}/{ticker}.parquet

Why this layering
-----------------
- :class:`TushareFetcher` is the only thing in the A-share survivorship
  pipeline that talks to Tushare's network APIs. Downstream Phase A.2
  (delisted registry builder), Phase A.4 (index membership resolver),
  and Phase B.2 (qlib bin builder) read these on-disk parquet dumps
  and never touch Tushare directly. That isolates the vendor: a future
  Wind / choice ingestion is a one-class change.
- The fetcher is intentionally dumb. It does NOT classify ``delist_reason``,
  does NOT detect ticker reuse (A-share has none — see PR 95), does NOT
  build a registry. Those are Phase A.2 concerns.

Scope (Phase A.1)
-----------------
- Pull the endpoints listed above into one output directory.
- Resume on re-run: per-file existence check; existing parquet files are
  skipped. Atomic writes via ``.tmp`` + rename so a killed process does
  not leave half-written files.
- Rate-limit handling: per-call sleep (``rate_limit_sleep_ms``) plus
  bounded retry on Tushare's typical rate-limit failure shapes (the
  client raises ``TushareClientError`` with messages containing 'rate',
  'limit', or 'returned None').
- Endpoint subset selection: callers can fetch any subset of the
  endpoints (e.g. for smoke-testing or incremental refresh).

The ``daily_basic`` endpoint is the per-(ticker, day) fundamentals
snapshot (PE/PB/turnover/market-cap). It uses the same per-(ticker,
year) pattern as ``daily`` / ``adj_factor`` and lives under
``daily_basic/{year}/{ticker}.parquet``. See PR #182's proposal for
the rationale (factor-mining feature-universe extension).

Out of scope for Phase A.1
--------------------------
- No registry build, no entity-aware logic, no NaN-gap padding (those
  are Phase A.2 / B.2).
- No qlib bin writes. This module's output is parquet, consumed by
  Phase A.2-B.2.
- No automatic delist_reason inference. Phase A.2 owns classification.
- No deletion of stale output. Resume is additive; the operator manages
  cleanup of the output dir if a full re-pull is wanted.
- No `_validate_pit_data` / cross-endpoint consistency checks. Phase B.3
  owns validation.

Token discipline
----------------
``TUSHARE_TOKEN`` is read by :class:`TushareClient.from_environment`. This
module never accepts a literal token in its config — passing a token
through ``TushareFetcherConfig`` is explicitly prohibited (would be a
secrets-in-config violation).
"""

from __future__ import annotations

import bisect
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from src.core.logger import get_logger
from src.data._atomic_io import atomic_write_parquet
from src.data.tushare.client import (
    KIND_NETWORK,
    KIND_RATE_LIMIT,
    KIND_SERVER_ERROR,
    TushareClient,
    TushareClientError,
)

# Moved to the dependency-free fetch_types module (P3-6b) so that reading a
# manifest / integrity stamp never imports this network stack; re-exported
# here unchanged (the `as` form marks an explicit re-export for mypy) for
# every existing importer.
from src.data.tushare.fetch_types import FetchHole as FetchHole  # noqa: F401
from src.data.tushare.fetch_types import (  # noqa: F401
    TushareFetchResult as TushareFetchResult,
)

_logger = get_logger(__name__)


# Public endpoint identifiers; order is also the execution order chosen by
# the orchestrator (cheap calls first; long poles last so failures surface
# fast).
ENDPOINTS: tuple[str, ...] = (
    "stock_basic",
    "namechange",
    "suspend_d",
    "index_weight",
    "daily",
    "adj_factor",
    "daily_basic",
)

DEFAULT_INDICES: tuple[str, ...] = (
    "000300.SH",  # CSI300
    "000905.SH",  # CSI500
    "000906.SH",  # CSI800
)

# Tushare 5000-point Pro tier handles ~500 calls/min on `daily`. We default
# to 200ms per call (300 calls/min) for headroom. Callers tune via config.
DEFAULT_RATE_LIMIT_SLEEP_MS = 200

# Backoff on rate-limit failures: first retry waits 60s, second 120s, etc.
RATE_LIMIT_BACKOFF_SECONDS = 60
MAX_RATE_LIMIT_RETRIES = 5

# P3-7b systemic-shortfall gate (PR #240 round 5, 拍死选项1): when MORE than
# this share of an endpoint's re-checked re-pulls still end short of their
# expected boundary, the shortfall is SYSTEMIC — a pre-close run fetching
# before today's bars are published, or vendor-side truncation — and becomes
# an ENDPOINT hole (run exits 3; the P3-4c build gate refuses the dump).
# Routine idiosyncratic shorts (a handful of tickers suspended through the
# slice end, or delisted with the last bar before the delist date) sit far
# below it (~0.5% of the universe on a typical day) and stay a loud warning.
SYSTEMIC_SHORTFALL_RATIO = 0.20
# A ratio is meaningless on tiny samples (one suspended ticker in a 3-unit
# targeted run is 33%): below this many re-checked units the shortfall is
# always treated as idiosyncratic (warning only).
SYSTEMIC_SHORTFALL_MIN_CHECKED = 50
# PR #271 — recency scope. The two failures the gate targets (a PRE-CLOSE run,
# vendor truncation of THIS pull) both leave ACTIVELY-TRADING tickers short of
# an expected boundary AT the run's end_date. A multi-year catch-up, by
# contrast, re-pulls years of legitimately-short files (suspended through a
# year-end, delisted mid-year) whose expected boundary — capped by
# _expected_year_file_end at the delist date / year-end last trading day — sits
# MONTHS in the past. Counting those toward the ratio tripped the gate on every
# full backfill (阶段1 forensics: 93% of shorts ended >20 trading days before
# run end; only ~10-12 were the real recent signature). So the SYSTEMIC ratio is
# computed ONLY over re-pulls whose expected boundary lands within the last N
# trading days of end_date; historical shorts self-exclude (their boundary is
# old) and stay the loud per-unit warning. A genuine pre-close run still puts
# every active ticker's boundary AT end_date → all in-window → still fires.
SYSTEMIC_SHORTFALL_RECENT_TRADING_DAYS = 5
# PR #271 — full-year-truncation guard. The recency window above cannot see a
# vendor truncating an entire year THIS RUN RE-PULLS whose boundary is old (a
# first-fetch, force-retried, or stale past year) — the C-class silent
# truncation PR #240's gate must still catch. (It does NOT cover a year already
# attested complete by the prior manifest watermark: those are blind-skipped,
# not re-pulled — use --verify-all-years to re-scan them.) The discriminator is
# the FRACTION OF THE YEAR'S UNIVERSE that comes back short. This must clear the
# largest LEGITIMATE shortfall a single year can have: a market-wide suspension
# wave. In the 2015 H2 A-share crash ~50% of listings halted at the peak (far
# fewer remained halted THROUGH year-end), so the threshold sits well above that
# — only a near-total wipeout (a true whole-year truncation is ~100%, every
# ticker short) trips it. (Measuring against the universe, NOT the re-checked
# subset, is essential: on a backfill the re-checked subset is dominated by the
# suspensions themselves, so a ratio over it would false-trip — the very bug the
# recency scope fixes. _expected_year_file_end caps the boundary only by
# delist_date, never by suspension, so suspended-through-year-end tickers DO
# count as short — hence the high bar.)
SYSTEMIC_SHORTFALL_FULL_YEAR_RATIO = 0.90

# Stock_basic field list for both 'L' and 'D' buckets. ts_code, list_date,
# delist_date are the load-bearing fields for Phase A.2; the rest are
# kept for diagnostics.
STOCK_BASIC_FIELDS = (
    "ts_code,symbol,name,area,industry,market,list_date,delist_date,"
    "list_status,curr_type"
)


class TushareFetcherError(RuntimeError):
    """Raised when the fetcher cannot continue (unexpected vendor error,
    malformed config, missing prerequisite, a NON-retryable hard error, etc.).
    Distinct from :class:`TushareClientError` (which is the per-call boundary)
    so callers can distinguish fetcher-orchestration failures from raw Tushare
    failures."""


class FetchHoleError(RuntimeError):
    """Raised by :meth:`TushareFetcher._safe_call` when a RETRYABLE call
    exhausts its retries.

    This is the *recoverable* failure (P3-4a continue-on-error): the calling
    per-endpoint loop records a hole (:class:`FetchHole`) and continues to the
    next unit rather than aborting the whole run — one transient blip no longer
    kills a multi-hour backfill. A NON-retryable :class:`TushareClientError`
    (token / permission / param) is re-raised by ``_safe_call`` instead and
    aborts fast — there is no point hammering a call that will fail identically.

    Carries only the retry outcome; the semantic unit (ticker / year / index /
    status) is supplied by the catching loop, which holds that context.
    """

    def __init__(
        self, api_name: str, *, reason_class: str, attempts: int, last_error: str,
    ) -> None:
        super().__init__(
            f"Tushare {api_name} exhausted {attempts} retryable attempts "
            f"({reason_class}); recorded as a hole. Last error: {last_error}"
        )
        self.api_name = api_name
        self.reason_class = reason_class
        self.attempts = attempts
        self.last_error = last_error


@dataclass(frozen=True)
class TushareFetcherConfig:
    """Configuration for :class:`TushareFetcher`.

    The token is NEVER part of this config — :class:`TushareClient` reads
    it from the environment. Passing a token through here is a
    secrets-in-config violation.
    """

    output_dir: Path
    start_date: str = "20000101"  # YYYYMMDD; per design doc §5 Stage 1
    end_date: str = "20251231"
    endpoints: tuple[str, ...] = ENDPOINTS
    indices: tuple[str, ...] = DEFAULT_INDICES
    rate_limit_sleep_ms: int = DEFAULT_RATE_LIMIT_SLEEP_MS
    dry_run: bool = False
    # When True, write empty parquet placeholders for tickers with no
    # daily / adj_factor rows in a year so resume can skip them on rerun.
    # When False, skip those tickers entirely (re-pulled on every run).
    write_empty_placeholders: bool = True
    # Injectable "today" for the stock_basic snapshot_date stamp (P3-5) —
    # value-injection as elsewhere (Phase 2 staleness guard): tests pass a fixed
    # date; production leaves None -> the system date at fetch time.
    now: date | None = None
    # P3-6a: ignore resume's exists-skip for the AGGREGATE units a daily update
    # must bring current — stock_basic (both buckets) and the namechange /
    # suspend_d aggregates. The per-ticker endpoints (daily / adj_factor /
    # daily_basic) no longer need this flag: their currency is decided per
    # (ticker, year) file by the max(trade_date) freshness rule (P3-7b), which
    # re-pulls exactly the year files whose content stops short of what this
    # run's range expects. index_weight is NOT refreshed (one file per index
    # over the full range; re-pulling all of them every day is hundreds of
    # calls — refresh membership deliberately / on its own cadence).
    refresh_current: bool = False
    # (endpoint, unit) pairs that MUST bypass the exists-skip this run — the
    # prior manifest's recorded holes, wired in by the 01 CLI. A refresh
    # failure leaves YESTERDAY's file on disk plus a manifest hole; the
    # freshness rule re-attempts stale files wherever it scans, but a holed
    # unit must be re-attempted even in years the scan scope skips (codex P1).
    # Forcing known-holed units past the skip re-attempts them every run until
    # they heal.
    force_retry_units: frozenset[tuple[str, str]] = frozenset()
    # P3-7b scan scope: per-endpoint (start, end) YYYYMMDD coverage ranges the
    # prior manifest already attests, wired in by the 01 CLI. A PAST year is
    # not re-scanned only when its whole expected slice lies INSIDE the
    # attested range — both `floor(year) <= end` AND `slice_start >= start`
    # (codex P1 on PR #240: an end-only watermark would silently trust
    # never-attested years BEFORE the prior coverage start on a backward
    # backfill). The FINAL requested year is always scanned. Empty mapping ->
    # every year of the requested range is scanned (first run / no manifest /
    # fresh start).
    assume_verified_ranges: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    # P3-7b escape hatch: scan EVERY year of the requested range regardless of
    # the watermark — for suspected external mutation of the dump, or a
    # pre-P3-7b manifest whose coverage_end_date may over-claim (written by a
    # run that advanced coverage without verifying per-file content).
    verify_all_years: bool = False

    def __post_init__(self) -> None:
        bad = tuple(e for e in self.endpoints if e not in ENDPOINTS)
        if bad:
            raise TushareFetcherError(
                f"Unknown endpoint(s) {bad!r}; valid: {ENDPOINTS}"
            )
        if not self.start_date.isdigit() or len(self.start_date) != 8:
            raise TushareFetcherError(
                f"start_date must be YYYYMMDD digits, got {self.start_date!r}"
            )
        if not self.end_date.isdigit() or len(self.end_date) != 8:
            raise TushareFetcherError(
                f"end_date must be YYYYMMDD digits, got {self.end_date!r}"
            )
        if self.start_date > self.end_date:
            raise TushareFetcherError(
                f"start_date {self.start_date} > end_date {self.end_date}"
            )
        if self.rate_limit_sleep_ms < 0:
            raise TushareFetcherError(
                f"rate_limit_sleep_ms must be >= 0, got {self.rate_limit_sleep_ms}"
            )


def _clean_yyyymmdd(value: Any) -> str | None:
    """Normalize a stock_basic date cell to a YYYYMMDD string, else None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return text
    return None


def _last_weekday_str(yyyymmdd: str) -> str:
    """Latest weekday (Mon-Fri) on or before ``yyyymmdd``, as YYYYMMDD.

    FALLBACK ONLY — used by ``_last_trading_day_on_or_before`` when the real
    exchange calendar (trade_cal) is unavailable. It is a holiday-UNAWARE
    approximation of the last trading day: the once-assumed invariant "the last
    December weekday is always a trading day" is FALSE (2018-12-31 Mon was a
    market holiday; the real last 2018 bar is 2018-12-28), which is exactly the
    false systemic-shortfall this module's calendar floor now avoids. When this
    fallback over-expects a holiday weekday, complete year files re-pull as
    bounded churn — never a silent skip of real data, and never (on its own) a
    build-blocking hole once the calendar path is available.
    """
    d = datetime.strptime(yyyymmdd, "%Y%m%d").date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def _last_trading_day_on_or_before(
    yyyymmdd: str, trading_days: Sequence[str] | None
) -> str | None:
    """Latest actual TRADING day (YYYYMMDD) on or before ``yyyymmdd``.

    ``trading_days`` is the sorted exchange calendar. When it is ``None`` (the
    calendar could not be fetched — e.g. trade_cal access failed) this falls
    back to :func:`_last_weekday_str`, preserving the prior behaviour.

    The weekday floor over-expects only when a slice's last weekday is a market
    HOLIDAY: 2018-12-31 (Mon) was closed, so the real last 2018 bar is
    2018-12-28 — yet the weekday floor expected 2018-12-31 and flagged every
    complete 2018 file as short, tripping the systemic-shortfall gate for a year
    that can never produce a later bar. A trading-day floor is exact.
    """
    if trading_days is None:
        return _last_weekday_str(yyyymmdd)
    idx = bisect.bisect_right(trading_days, yyyymmdd)
    if idx == 0:
        return None
    return trading_days[idx - 1]


def _clip_slice_to_window(
    year_start: str,
    year_end: str,
    window: tuple[str | None, str | None],
) -> tuple[str, str] | None:
    """Clip the run's ``[year_start, year_end]`` slice by the ticker's listing
    ``window``, or ``None`` when the window MISSES the slice entirely.

    A ``None`` return means the ticker was listed after / delisted before the
    slice, so no data can exist for it — distinct from "the slice exists but
    has no trading day" (handled by the caller via the calendar floor). The two
    None reasons must stay separable: a window-miss makes an empty placeholder
    the truthful content (a spurious non-empty file is re-pulled to empty),
    whereas a no-trading-day slice carries no such claim and must NOT clobber a
    file that may hold real data for a wider range (codex P1).
    """
    list_date, delist_date = window
    lo = year_start
    hi = year_end
    if list_date is not None and list_date > lo:
        lo = list_date
    if delist_date is not None and delist_date < hi:
        hi = delist_date
    return None if lo > hi else (lo, hi)


def _expected_year_file_end(
    *,
    year_start: str,
    year_end: str,
    window: tuple[str | None, str | None],
    trading_days: Sequence[str] | None = None,
) -> str | None:
    """Latest ``trade_date`` this run can expect inside a (ticker, year) file,
    or ``None`` when no boundary can be claimed for the slice.

    ``year_start`` / ``year_end`` are the run's CLIPPED slice for the year
    (already bounded by the config range). The ticker's listing ``window``
    further bounds it: listed-after-slice or delisted-before-slice ⇒ ``None``
    (an empty placeholder is the truthful content); delisted mid-slice caps
    the expectation at the delist date. The result is floored to the last actual
    TRADING day (``trading_days``; the last-weekday heuristic only when no
    calendar is available) — and is ``None`` when the slice holds no trading day
    at all. Note the cap errs toward re-fetching: a ticker suspended through
    its slice end (or whose final bar precedes its delist date) yields a file
    that genuinely ends early and is re-pulled on every scan of that year —
    bounded waste, never a silent skip of real data.
    """
    clipped = _clip_slice_to_window(year_start, year_end, window)
    if clipped is None:
        return None
    lo, hi = clipped
    floored = _last_trading_day_on_or_before(hi, trading_days)
    if floored is None or floored < lo:
        return None
    return floored


def _recent_boundary_floor(
    end_date: str, trading_days: Sequence[str] | None, n_trading_days: int
) -> str:
    """Lower bound (YYYYMMDD) for an EXPECTED boundary to count as "recent" for
    the systemic-shortfall gate: a boundary ``>=`` the returned floor lies within
    the last ``n_trading_days`` trading days on or before ``end_date``.

    With the exchange calendar this is exact. Without it (calendar unavailable
    or empty) it falls back to a generous calendar-day window that safely spans
    ``n_trading_days`` trading days across a holiday week — the gate only needs
    to separate boundaries AT the run end (a pre-close run / current-pull
    truncation) from boundaries MONTHS in the past (historical suspensions and
    delistings), and that gap dwarfs any approximation error.
    """
    if trading_days:
        # Number of calendar trading days on or before end_date.
        idx = bisect.bisect_right(trading_days, end_date)
        if idx == 0:
            # end_date precedes the whole calendar (not a real run) — treat the
            # earliest day as the floor so the window degrades to "everything".
            return trading_days[0]
        return trading_days[max(0, idx - n_trading_days)]
    # No calendar: a generous calendar-day window. N trading days can span a
    # long CN closure (Spring Festival / National Day reach ~10 consecutive
    # closed days) plus weekends — worst case ~18 calendar days for N=5 — so the
    # margin is deliberately wider than that. Over-inclusion only widens the
    # gate on this rare degraded path; it never reintroduces the backfill
    # false-positive (historical boundaries are still months too old to land in
    # the window).
    anchor = datetime.strptime(end_date, "%Y%m%d").date()
    floor = anchor - timedelta(days=n_trading_days * 3 + 14)
    return floor.strftime("%Y%m%d")


class TushareFetcher:
    """Orchestrator that pulls the Phase A.1 endpoints into a directory.

    Construction is cheap; ``fetch()`` does the work. Each endpoint method
    is also callable on its own for testing / incremental refresh.
    """

    def __init__(self, client: TushareClient, config: TushareFetcherConfig) -> None:
        self._client = client
        self._config = config
        # Units skipped after exhausting retryable retries (continue-on-error,
        # P3-4a). Reset at the start of every fetch(). In-memory only — P3-4b
        # persists these to a fetch manifest.
        self._holes: list[FetchHole] = []
        # SSE trading calendar (sorted YYYYMMDD), fetched once per run and used
        # to floor freshness boundaries to the actual last TRADING day rather
        # than the last weekday (which is wrong when a year's last weekday is a
        # market holiday, e.g. 2018-12-31 Mon). None = trade_cal unavailable →
        # weekday-floor fallback. _loaded guards a single fetch attempt.
        self._trading_days: tuple[str, ...] | None = None
        self._trading_days_loaded = False

    @property
    def holes(self) -> tuple[FetchHole, ...]:
        """Units skipped after exhausting retryable retries during the last
        ``fetch()`` (or a direct ``_fetch_*`` call). Empty == a complete pull.
        The CLI turns a non-empty result into a non-zero exit + a hole report,
        so a holey dump is never mistaken for a complete one."""
        return tuple(self._holes)

    # ------------------------------------------------------------------
    # Public orchestrator
    # ------------------------------------------------------------------

    def fetch(self) -> list[TushareFetchResult]:
        """Pull every endpoint in the configured set, in fixed order.

        Continue-on-error (P3-4a): a unit (ticker+year, index, status) whose
        call EXHAUSTS its retryable retries is recorded as a hole
        (:attr:`holes`) and the loop continues — one transient blip no longer
        aborts a multi-hour backfill. A NON-retryable error (token / permission
        / param) still aborts fast (it would fail identically on every unit).
        Resume stays per-file-existence; the CLI returns non-zero + a hole
        report when :attr:`holes` is non-empty, so a holey dump is never
        mistaken for a complete one. Inspect :attr:`holes` after the call.
        """
        self._holes = []
        # Honour --dry-run: do NOT create output_dir (Codex review #99
        # PR comment). dry-run promises no filesystem side-effects.
        if not self._config.dry_run:
            self._config.output_dir.mkdir(parents=True, exist_ok=True)
        results: list[TushareFetchResult] = []
        for endpoint in ENDPOINTS:
            if endpoint not in self._config.endpoints:
                _logger.info("Skipping endpoint %r (not in config.endpoints)", endpoint)
                continue
            _logger.info("=== endpoint: %s ===", endpoint)
            method = getattr(self, f"_fetch_{endpoint}")
            results.append(method())
        return results

    # ------------------------------------------------------------------
    # Per-endpoint methods (also callable directly for testing)
    # ------------------------------------------------------------------

    def _must_retry(self, endpoint: str, unit: str) -> bool:
        """True when a prior-manifest hole forces this unit past the
        exists-skip (codex P1: a refresh failure leaves yesterday's file on
        disk, and after a year boundary the unit would otherwise be shadowed
        forever while the merge drops its never-re-attempted hole)."""
        return (endpoint, unit) in self._config.force_retry_units

    def _aggregate_can_skip(
        self,
        path: Path,
        endpoint: str,
        unit: str,
        *,
        honor_refresh_current: bool = True,
    ) -> bool:
        """Whether an existing single-file aggregate may be resume-skipped.

        Skip iff the file exists, no prior-manifest hole forces a re-attempt of
        this ``unit``, and — for endpoints that honour it — ``--refresh-current``
        was not requested. ``index_weight`` passes ``honor_refresh_current=False``
        because it is exempt from refresh-current (one full-range file per index;
        re-pulling all of them every day is hundreds of calls).
        """
        if not path.exists():
            return False
        if honor_refresh_current and self._config.refresh_current:
            return False
        return not self._must_retry(endpoint, unit)

    def _fetch_single_file_aggregate(
        self, *, endpoint: str, filename: str, fields: str,
    ) -> TushareFetchResult:
        """Fetch a single-file aggregate endpoint (``namechange`` / ``suspend_d``).

        One call covers ``[start_date, end_date]`` and writes one parquet. The
        hole unit is the stable ``"file"``: the whole file IS the unit, so a
        re-failure matches the prior hole (the run's range varies and lives in
        the manifest coverage fields, not the unit — codex P2).
        """
        path = self._config.output_dir / filename
        if self._aggregate_can_skip(path, endpoint, "file"):
            _logger.info("  skip (exists): %s", path)
            return TushareFetchResult(endpoint, 0, 0, skipped=1)
        if self._config.dry_run:
            _logger.info("  [dry-run] would write %s", path)
            return TushareFetchResult(endpoint, 0, 0, skipped=0)
        try:
            df = self._safe_call(
                endpoint,
                start_date=self._config.start_date,
                end_date=self._config.end_date,
                fields=fields,
            )
        except FetchHoleError as hole:
            self._record_hole(endpoint, "file", hole)
            return TushareFetchResult(endpoint, 0, 0, skipped=0)
        atomic_write_parquet(df, path)
        _logger.info("  wrote %d rows to %s", len(df), path)
        return TushareFetchResult(endpoint, 1, len(df))

    def _fetch_stock_basic(self) -> TushareFetchResult:
        """Pull both 'L' (active) and 'D' (delisted) buckets as separate files."""
        written = 0
        rows = 0
        skipped = 0
        for label, status in [("active_stocks", "L"), ("delisted_stocks", "D")]:
            path = self._config.output_dir / f"{label}.parquet"
            unit = f"list_status={status} ({label})"
            if (path.exists() and not self._config.refresh_current
                    and not self._must_retry("stock_basic", unit)):
                _logger.info("  skip (exists): %s", path)
                skipped += 1
                continue
            if self._config.dry_run:
                _logger.info("  [dry-run] would write %s (list_status=%r)", path, status)
                continue
            try:
                df = self._safe_call(
                    "stock_basic",
                    exchange="",
                    list_status=status,
                    fields=STOCK_BASIC_FIELDS,
                )
            except FetchHoleError as hole:
                self._record_hole("stock_basic", f"list_status={status} ({label})", hole)
                continue
            # P3-5: embed the snapshot date IN the file (YYYYMMDD, one value for
            # every row). Downstream staleness guards previously had only the file
            # mtime — a weak proxy a sync/copy tool can silently refresh; an
            # embedded column survives copies and pandas round-trips. Injectable
            # via config.now (value-injection); production = system date.
            snapshot = self._config.now if self._config.now is not None else date.today()
            df = df.assign(snapshot_date=snapshot.strftime("%Y%m%d"))
            atomic_write_parquet(df, path)
            _logger.info("  wrote %d rows to %s", len(df), path)
            written += 1
            rows += len(df)
        return TushareFetchResult("stock_basic", written, rows, skipped)

    def _fetch_namechange(self) -> TushareFetchResult:
        """Pull all name changes in [start_date, end_date]. One call."""
        return self._fetch_single_file_aggregate(
            endpoint="namechange",
            filename="all_namechanges.parquet",
            fields="ts_code,name,start_date,end_date,ann_date,change_reason",
        )

    def _fetch_suspend_d(self) -> TushareFetchResult:
        """Pull suspend / resume history in [start_date, end_date]."""
        return self._fetch_single_file_aggregate(
            endpoint="suspend_d",
            filename="suspend_d.parquet",
            fields="ts_code,trade_date,suspend_timing,suspend_type",
        )

    def _fetch_index_weight(self) -> TushareFetchResult:
        """Pull index_weight per configured index across the date range.

        Tushare returns monthly snapshots, BUT the ``index_weight`` endpoint
        caps the per-call payload at ~6000 rows. CSI300 with ~300
        constituents per snapshot fits ~20 snapshots per call, so a
        multi-year date range would silently truncate to the most recent
        ~20 months (discovered during Phase A.4 smoke test against real
        Tushare). We chunk by year and concat to defeat the cap, writing
        one parquet per index as the contract still requires.
        """
        written = 0
        rows = 0
        skipped = 0
        out_root = self._config.output_dir / "index_weight"
        start_year = int(self._config.start_date[:4])
        end_year = int(self._config.end_date[:4])
        for idx in self._config.indices:
            path = out_root / f"{idx}.parquet"
            # index_weight is exempt from refresh_current (honor_refresh_current
            # =False), but a prior-manifest hole still forces a re-attempt (its
            # file may be yesterday's).
            if self._aggregate_can_skip(
                path, "index_weight", f"index={idx}", honor_refresh_current=False,
            ):
                _logger.info("  skip (exists): %s", path)
                skipped += 1
                continue
            if self._config.dry_run:
                _logger.info("  [dry-run] would write %s (chunked %d-%d)",
                             path, start_year, end_year)
                continue
            chunks: list[pd.DataFrame] = []
            holed = False
            for year in range(start_year, end_year + 1):
                y_start = f"{year}0101"
                y_end = f"{year}1231"
                if y_start < self._config.start_date:
                    y_start = self._config.start_date
                if y_end > self._config.end_date:
                    y_end = self._config.end_date
                try:
                    chunk = self._safe_call(
                        "index_weight",
                        index_code=idx,
                        start_date=y_start,
                        end_date=y_end,
                        fields="index_code,con_code,trade_date,weight",
                    )
                except FetchHoleError as hole:
                    # index_weight writes ONE file per index, so a hole is the
                    # WHOLE index (a partial file would be SKIPPED by resume and
                    # the hole never filled — a re-run re-fetches the whole index).
                    # The unit is the index ALONE, NOT the first-failing year:
                    # that year varies run-to-run (whichever transient failure
                    # hits first), so including it would make a re-run's hole look
                    # like a different unit and the manifest merge would drop the
                    # prior un-healed hole as if self-healed (codex P1).
                    self._record_hole("index_weight", f"index={idx}", hole)
                    holed = True
                    break
                if not chunk.empty:
                    chunks.append(chunk)
            if holed:
                continue
            if not chunks:
                # No data across the whole range — write empty parquet so
                # resume on a subsequent run skips this index.
                df = pd.DataFrame(
                    columns=["index_code", "con_code", "trade_date", "weight"]
                )
            else:
                df = pd.concat(chunks, ignore_index=True)
            atomic_write_parquet(df, path)
            _logger.info(
                "  wrote %d rows to %s (across %d yearly chunks)",
                len(df), path, len(chunks),
            )
            written += 1
            rows += len(df)
        return TushareFetchResult("index_weight", written, rows, skipped)

    def _fetch_daily(self) -> TushareFetchResult:
        """Pull daily OHLCV per (ticker, year). Long pole — supports resume."""
        return self._fetch_per_ticker_per_year(
            endpoint="daily",
            subdir="daily",
            fields="ts_code,trade_date,open,high,low,close,vol,amount",
        )

    def _fetch_adj_factor(self) -> TushareFetchResult:
        """Pull adj_factor per (ticker, year). Long pole — supports resume."""
        return self._fetch_per_ticker_per_year(
            endpoint="adj_factor",
            subdir="adj_factor",
            fields="ts_code,trade_date,adj_factor",
        )

    def _fetch_daily_basic(self) -> TushareFetchResult:
        """Pull daily_basic fundamentals per (ticker, year).

        Long pole — supports resume on per-file existence. Fields cover
        the factor-mining feature universe extension (PR #182): value
        ratios (pe/pb/ps/ps_ttm), microstructure (turnover_rate), size
        (circ_mv/total_mv), and share-count diagnostics
        (float_share/total_share). The fetched ``trade_date`` keys join
        these fundamentals to the OHLCV ladder downstream.
        """
        return self._fetch_per_ticker_per_year(
            endpoint="daily_basic",
            subdir="daily_basic",
            fields=(
                "ts_code,trade_date,turnover_rate,pe,pb,ps,ps_ttm,"
                "circ_mv,total_mv,float_share,total_share"
            ),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_trading_days(self) -> tuple[str, ...] | None:
        """SSE trading calendar (sorted YYYYMMDD) for the run's range, cached.

        Returns None when the calendar can't be fetched (trade_cal access fails,
        or a dry-run), and the freshness boundary falls back to the weekday
        floor. SSE and SZSE share the A-share trading calendar, so one fetch
        covers every ticker. Fetched at most once per run (``_loaded`` guard).
        """
        if self._trading_days_loaded:
            return self._trading_days
        self._trading_days_loaded = True
        if self._config.dry_run:
            return None
        # trade_cal is a BEST-EFFORT freshness optimisation: ANY failure — fetch
        # error or a malformed/unexpected response — must degrade to the weekday
        # floor, never break the fetch. The whole call+parse is therefore inside
        # one broad try. The call goes through _safe_call so a transient / rate-
        # limit blip on this single highest-leverage call (typically the FIRST
        # network action of a multi-hour backfill) is RETRIED rather than
        # permanently disabling the holiday-aware floor; on retryable exhaustion
        # _safe_call raises FetchHoleError, which we degrade here (no hole — the
        # weekday floor is a safe, if churnier, fallback).
        try:
            df = self._safe_call(
                "trade_cal",
                exchange="SSE",
                start_date=self._config.start_date,
                end_date=self._config.end_date,
                is_open="1",
            )
            # UNAVAILABLE / unexpected shape (None, non-frame, no cal_date
            # column) → degrade to the weekday fallback (None).
            cols = getattr(df, "columns", None)
            if df is None or cols is None or "cal_date" not in cols:
                _logger.warning(
                    "trade_cal returned no usable calendar — weekday-floor fallback."
                )
                return None
            raw = [str(d) for d in df["cal_date"]]
            # A legitimately EMPTY result (e.g. a slice that is entirely a
            # holiday — trade_cal is_open=1 returns zero rows) means NO trading
            # days. That is a VALID, EMPTY calendar — distinct from unavailable:
            # `_expected_year_file_end` with () floors to None (no boundary), so
            # nothing is expected and no file is re-pulled or flagged short. (A
            # weekday fallback here would instead expect the holiday and could
            # trip the systemic-shortfall gate — Codex P2.)
            if not raw:
                self._trading_days = ()
                return self._trading_days
            # A malformed / NaN / dash-formatted cal_date row means we CANNOT
            # trust this calendar as the authoritative exchange calendar. Degrade
            # the WHOLE calendar to the weekday fallback rather than silently
            # DROPPING the bad rows — a partial calendar could be missing a
            # slice's real last trading day, under-expect the boundary, and mark
            # a genuinely-stale/empty file as verified (silent loss of real
            # data, the dangerous direction). (Codex P1.) Duplicates of
            # well-formed dates are harmless and deduped.
            if any(not (s.isdigit() and len(s) == 8) for s in raw):
                _logger.warning(
                    "trade_cal contained malformed cal_date value(s) — "
                    "degrading the whole calendar to the weekday-floor fallback."
                )
                return None
            self._trading_days = tuple(sorted(set(raw)))
            return self._trading_days
        except Exception as exc:
            _logger.warning(
                "trade_cal unavailable (%s) — freshness boundaries fall back to "
                "the weekday floor (a year whose last weekday is a holiday may "
                "be needlessly re-pulled).",
                exc,
            )
            return None

    def _fetch_per_ticker_per_year(
        self, *, endpoint: str, subdir: str, fields: str,
    ) -> TushareFetchResult:
        """Common loop for ``daily`` / ``adj_factor`` / ``daily_basic`` — per (year, ticker)."""
        try:
            tickers = self._load_ticker_universe()
        except TushareFetcherError:
            # The ticker universe is unavailable. If stock_basic holed THIS run
            # (a transient failure left active/delisted incomplete), this
            # per-ticker endpoint cannot run through no fault of usage: record a
            # prerequisite hole and skip it (continue-on-error), so the run
            # completes-with-holes (exit 3) and a re-run fills stock_basic first,
            # then this endpoint. If stock_basic was simply never fetched (no
            # hole this run), it is a usage error — re-raise for the hard abort.
            if any(h.endpoint == "stock_basic" for h in self._holes):
                self._add_hole(
                    endpoint,
                    "prerequisite stock_basic incomplete",
                    reason_class="prerequisite",
                    attempts=0,
                    last_error="ticker universe unavailable: stock_basic holed this run",
                )
                return TushareFetchResult(endpoint, 0, 0, skipped=0)
            raise
        windows = self._load_ticker_windows()
        out_root = self._config.output_dir / subdir
        start_year = int(self._config.start_date[:4])
        end_year = int(self._config.end_date[:4])
        watermark = self._config.assume_verified_ranges.get(endpoint)
        written = 0
        rows = 0
        skipped = 0
        verified = 0
        stale_refetched = 0
        still_short: list[str] = []  # re-pulled units still short of expected
        rechecked = 0  # re-pulls whose post-write re-check actually ran
        # PR #271 — the SYSTEMIC subset: re-checks whose EXPECTED boundary is
        # recent (within the last N trading days of end_date). Only these can
        # signal a pre-close run / current-pull truncation; historical
        # suspensions/delists (old boundary) are excluded so a multi-year
        # backfill no longer false-trips the gate.
        still_short_recent: list[str] = []
        rechecked_recent = 0
        # PR #271 — per-year tallies for the full-year-truncation guard.
        # universe = every in-window (ticker, year) that can hold data;
        # short = those that came back short. A near-total per-year shortfall is
        # vendor truncation even when its boundary is old (recency can't see it).
        universe_by_year: dict[int, int] = {}
        short_by_year: dict[int, int] = {}
        # The exchange trading calendar floors freshness boundaries to the real
        # last TRADING day (not the last weekday). Fetched once per run (cached);
        # None ⇒ weekday-floor fallback. Shared across all three date-bounded
        # endpoints via the instance cache.
        tdays = self._get_trading_days()
        recent_boundary_floor = _recent_boundary_floor(
            self._config.end_date, tdays, SYSTEMIC_SHORTFALL_RECENT_TRADING_DAYS
        )
        for year in range(start_year, end_year + 1):
            year_dir = out_root / str(year)
            if not self._config.dry_run:
                year_dir.mkdir(parents=True, exist_ok=True)
            year_start = f"{year}0101"
            year_end = f"{year}1231"
            # Clip to config range for boundary years
            if year_start < self._config.start_date:
                year_start = self._config.start_date
            if year_end > self._config.end_date:
                year_end = self._config.end_date
            # P3-7b scan scope: the FINAL requested year is ALWAYS scanned for
            # freshness (a daily re-run must notice yesterday's file lacks
            # today's bar — and a post-close re-run must notice a pre-close
            # run's file lacks today's bar even though coverage already says
            # "today"). A PAST year is re-scanned unless its WHOLE expected
            # slice lies inside the prior manifest's attested range — both
            # ends checked (codex P1 on PR #240: end-only would trust
            # never-attested years before the coverage start on a backward
            # backfill). --verify-all-years forces the sweep; no watermark
            # (no / pre-P3-7b manifest) scans everything.
            _year_cap = min(self._config.end_date, f"{year}1231")
            # year_floor: the slice's expected last trading day, used only for
            # the watermark scan-gating below. A None from the calendar lookup
            # is a VALID "no trading day in this slice" (e.g. a slice that is
            # entirely a holiday on a cross-year range) — do NOT coerce it to the
            # weekday floor: that would re-scan a watermarked complete file and,
            # via the expected-None path, risk overwriting a real full-year file
            # with an empty pull (Codex P2). Fall back to the weekday floor ONLY
            # when the CALENDAR itself is unavailable (tdays is None).
            year_floor: str | None
            if tdays is None:
                year_floor = _last_weekday_str(_year_cap)
            else:
                year_floor = _last_trading_day_on_or_before(_year_cap, tdays)
            scan_year = (
                year == end_year
                or self._config.verify_all_years
                or watermark is None
                or (year_floor is not None and year_floor > watermark[1])
                or year_start < watermark[0]
            )
            for i, ticker in enumerate(tickers, 1):
                path = year_dir / f"{ticker}.parquet"
                unit = f"ts_code={ticker} year={year}"
                # The expected-content boundary for ANY unit this run fetches
                # (a stale re-pull, a force-retried existing file, or a
                # missing file fetched fresh): the written frame is re-checked
                # against it after the write (codex P1 rounds 3/4/7). None ⇒
                # no boundary exists (the listing window misses the slice) ⇒
                # no re-check, an empty response is the truth.
                recheck_boundary: str | None = None
                force_retry = self._must_retry(endpoint, unit)
                window = windows.get(ticker, (None, None))
                expected_boundary = _expected_year_file_end(
                    year_start=year_start,
                    year_end=year_end,
                    window=window,
                    trading_days=tdays,
                )
                if expected_boundary is not None:
                    # In-window ticker that can hold data this year → part of the
                    # year's universe (denominator for the full-year-truncation
                    # guard). Window-misses / no-trading-day slices are excluded.
                    universe_by_year[year] = universe_by_year.get(year, 0) + 1
                # A slice the listing window COVERS but that holds NO trading
                # day (a holiday-only re-run, e.g. --start 20181231 --end
                # 20181231) claims no boundary and can pull no data. If a file
                # already EXISTS it holds real data for the wider year, so an
                # empty pull would silently clobber it — preserve it on EVERY
                # path that would otherwise fetch and overwrite: the non-forced
                # freshness scan AND a forced retry of a prior-manifest hole
                # (codex P1 rounds 4-5). A MISSING file has nothing to lose and
                # still falls through to write an empty placeholder. (A
                # window-MISS keeps expected_boundary None too — but there the
                # empty placeholder IS the truth, handled in-branch below.)
                holiday_only_existing = (
                    path.exists()
                    and expected_boundary is None
                    and _clip_slice_to_window(year_start, year_end, window)
                    is not None
                )
                if path.exists() and not force_retry:
                    if not scan_year:
                        # Attested by the prior manifest's watermark — closed
                        # history this run cannot expect more from. A BLIND
                        # skip: proves nothing, establishes nothing.
                        skipped += 1
                        continue
                    if holiday_only_existing:
                        skipped += 1
                        verified += 1
                        continue
                    expected = expected_boundary
                    if expected is None:
                        # The ticker's listing window MISSES this year slice
                        # (the holiday-covers case is handled above) — no data
                        # can exist, so a readable EMPTY placeholder is the
                        # truthful content: POSITIVE knowledge → verified (codex
                        # P2: must establish coverage, unlike a blind resume
                        # skip). But VERIFY the placeholder before claiming it
                        # (codex P2 round 2): a corrupt blob or a file holding
                        # unexpected rows (external mutation / interrupted
                        # write) falls through to the re-pull, which overwrites
                        # it with a clean empty placeholder.
                        if self._placeholder_is_clean(path):
                            skipped += 1
                            verified += 1
                            continue
                        # Dirty placeholder → re-pull (no freshness compare:
                        # there is no expected date to compare against).
                        stale_refetched += 1
                    else:
                        file_max = self._read_file_max_trade_date(path)
                        if file_max is not None and file_max >= expected:
                            # Content POSITIVELY confirmed complete for this
                            # run's range — verified, establishes coverage
                            # (codex P2).
                            skipped += 1
                            verified += 1
                            continue
                        # Stale (max < expected), empty-but-data-possible, or
                        # unreadable: re-pull the WHOLE year (one API call —
                        # same cost as fetching a single day) and overwrite. A
                        # failure below leaves the old file in place and
                        # records a hole; the file stays stale, so the next
                        # run re-attempts it (self-healing without any extra
                        # bookkeeping).
                        stale_refetched += 1
                        recheck_boundary = expected
                else:
                    # MISSING file (first run / new ticker / new year) OR a
                    # force-retried existing file: both fetch below and both
                    # get the same post-write re-check (codex P1 rounds 4+7).
                    # Without this, a PRE-CLOSE FIRST run would write short
                    # current-year files with no re-check at all and record a
                    # complete manifest through today — the fresh-fetch
                    # entrance to the exact hole the systemic-shortfall gate
                    # closes for re-pulls. Window-misses-slice units keep the
                    # boundary None (an empty response is their truth).
                    if holiday_only_existing:
                        # A FORCED retry (prior-manifest hole) of an EXISTING
                        # file on a holiday-only slice: the hole is spurious (no
                        # data can exist for a no-trading-day slice) and an empty
                        # pull would clobber the real wider-year file. Preserve —
                        # the spurious hole self-heals on the next real-range run
                        # (codex P1 round 5).
                        skipped += 1
                        verified += 1
                        continue
                    recheck_boundary = expected_boundary
                if self._config.dry_run:
                    if i == 1:
                        _logger.info(
                            "  [dry-run] would pull %s for %d tickers × year %d",
                            endpoint, len(tickers), year,
                        )
                    continue
                try:
                    df = self._safe_call(
                        endpoint,
                        ts_code=ticker,
                        start_date=year_start,
                        end_date=year_end,
                        fields=fields,
                    )
                except FetchHoleError as hole:
                    self._record_hole(endpoint, f"ts_code={ticker} year={year}", hole)
                    continue
                if df.empty and not self._config.write_empty_placeholders:
                    continue
                atomic_write_parquet(df, path)
                written += 1
                rows += len(df)
                # codex P1 round 3: re-check a freshness-rule re-pull against
                # the boundary that made the old file stale. The verdict is
                # decided in AGGREGATE after the loop (round 5, 拍死 option 1):
                # an IDIOSYNCRATIC shortfall (a few tickers suspended through
                # the slice end, or delisted with the last bar before the
                # delist date — no more data exists for them) stays a loud
                # warning, while a SYSTEMIC one (a large share of re-checked
                # re-pulls short — a pre-close run before today's bars are
                # published, or vendor-side truncation) becomes an endpoint
                # hole that fails the run and the downstream build gate.
                if recheck_boundary is not None and "trade_date" in df.columns:
                    rechecked += 1
                    # Recent ⇒ this boundary is close to end_date, the only
                    # place a pre-close run / current-pull truncation shows up
                    # (PR #271). Historical boundaries (suspension/delist-capped)
                    # are old and excluded from the systemic ratio.
                    boundary_is_recent = recheck_boundary >= recent_boundary_floor
                    if boundary_is_recent:
                        rechecked_recent += 1
                    new_max = str(df["trade_date"].max()) if len(df) else None
                    if new_max is None or new_max < recheck_boundary:
                        still_short.append(unit)
                        short_by_year[year] = short_by_year.get(year, 0) + 1
                        if boundary_is_recent:
                            still_short_recent.append(unit)
                if i % 200 == 0:
                    _logger.info(
                        "  %s year=%d progress: %d/%d tickers (written=%d, skipped=%d)",
                        endpoint, year, i, len(tickers), written, skipped,
                    )
        if stale_refetched or verified:
            _logger.info(
                "  %s: freshness rule verified %d existing year file(s) "
                "complete and re-pulled %d stale/incomplete one(s) (P3-7b).",
                endpoint, verified, stale_refetched,
            )
        if still_short:
            # SYSTEMIC decision (PR #271) — two independent shapes trip it:
            #  (1) RECENT: a large share of re-pulls whose expected boundary sits
            #      within the last N trading days of end_date came back short —
            #      a PRE-CLOSE run or current-pull truncation. Historical
            #      suspensions/delists (old boundary) are excluded here.
            #  (2) FULL-YEAR: a single year where the shortfall covers more than
            #      SYSTEMIC_SHORTFALL_FULL_YEAR_RATIO of that year's UNIVERSE —
            #      vendor truncation of a whole (possibly past) year, which the
            #      recency window cannot see. Measured vs the universe, not the
            #      re-checked subset, so scattered legitimate shorts never trip it.
            # Anything else (years of accumulated suspensions/delists) stays the
            # loud per-unit warning — never a build-blocking hole.
            recent_ratio = (
                len(still_short_recent) / rechecked_recent
                if rechecked_recent
                else 0.0
            )
            recent_systemic = (
                rechecked_recent >= SYSTEMIC_SHORTFALL_MIN_CHECKED
                and recent_ratio > SYSTEMIC_SHORTFALL_RATIO
            )
            truncated_years = sorted(
                y for y, s in short_by_year.items()
                if s >= SYSTEMIC_SHORTFALL_MIN_CHECKED
                and s / universe_by_year[y] > SYSTEMIC_SHORTFALL_FULL_YEAR_RATIO
            )
            if recent_systemic or truncated_years:
                # Record an ENDPOINT hole so the run exits 3 and the P3-4c build
                # gate refuses the dump.
                if recent_systemic:
                    detail = (
                        f"{len(still_short_recent)}/{rechecked_recent} "
                        f"re-checked re-pulls with a RECENT expected boundary "
                        f"(within {SYSTEMIC_SHORTFALL_RECENT_TRADING_DAYS} "
                        f"trading days of end_date) still end before it "
                        f"(pre-close run before today's bars are published, or "
                        f"vendor-side truncation?). First: "
                        + "; ".join(still_short_recent[:3])
                    )
                else:
                    y0 = truncated_years[0]
                    detail = (
                        f"year(s) {truncated_years} came back almost entirely "
                        f"short ({short_by_year[y0]}/{universe_by_year[y0]} of "
                        f"year {y0}'s in-window universe) — whole-year "
                        f"vendor-side truncation."
                    )
                self._add_hole(
                    endpoint,
                    "systemic-shortfall",
                    reason_class="systemic_shortfall",
                    attempts=1,
                    last_error=detail[:300],
                )
            else:
                # Idiosyncratic (or historical-only): the vendor's complete
                # answer for tickers suspended through a slice end or delisted
                # before their delist date. On a multi-year backfill this is
                # where the years of accumulated legitimate shorts land. Loud,
                # never a build-blocking hole (PR #271 keeps this signal so
                # silent deep-history truncation stays visible).
                _logger.warning(
                    "  %s: %d of %d re-checked re-pull(s) STILL end before "
                    "their expected boundary after a fresh full-year pull "
                    "(%d of them with a recent boundary; first: %s). Below the "
                    "systemic threshold, this is the vendor's complete answer — "
                    "normal for tickers suspended through a slice end or "
                    "delisted before their delist date; if neither applies, "
                    "suspect silent vendor truncation and re-run with "
                    "--verify-all-years after checking the source.",
                    endpoint, len(still_short), rechecked,
                    len(still_short_recent), "; ".join(still_short[:3]),
                )
        return TushareFetchResult(
            endpoint, written, rows, skipped, units_verified=verified,
        )

    def _read_file_max_trade_date(self, path: Path) -> str | None:
        """Latest ``trade_date`` (YYYYMMDD string) inside an existing year
        file, or ``None`` when the file is an empty placeholder, lacks the
        column, or cannot be read (all three mean "cannot confirm complete" —
        the freshness rule then re-pulls the year, which also self-heals a
        corrupt file by overwriting it)."""
        try:
            frame = pd.read_parquet(path, columns=["trade_date"])
        except Exception as exc:  # noqa: BLE001 — any unreadable shape → refetch
            _logger.warning(
                "  Could not read trade_date from %s (%s) — treating as stale "
                "and re-pulling the year.", path, exc,
            )
            return None
        if frame.empty:
            return None
        value = frame["trade_date"].max()
        if pd.isna(value):
            return None
        return str(value)

    def _placeholder_is_clean(self, path: Path) -> bool:
        """True iff an expected-no-data placeholder is a READABLE parquet with
        ZERO rows. A corrupt blob or a file holding unexpected rows (external
        mutation / interrupted write) is dirty — the caller re-pulls the year,
        which overwrites it with a clean empty placeholder (codex P2 round 2
        on PR #240: claiming such a file "verified" would advance coverage
        over an unreadable/wrong file and bypass the unreadable-file re-pull
        path)."""
        try:
            frame = pd.read_parquet(path)
        except Exception as exc:  # noqa: BLE001 — any unreadable shape → refetch
            _logger.warning(
                "  Unreadable no-data placeholder %s (%s) — re-pulling the "
                "year to rewrite it.", path, exc,
            )
            return False
        if len(frame) > 0:
            _logger.warning(
                "  No-data placeholder %s unexpectedly holds %d row(s) "
                "(listing window says none can exist) — re-pulling the year "
                "to rewrite it.", path, len(frame),
            )
            return False
        return True

    def _load_ticker_windows(self) -> dict[str, tuple[str | None, str | None]]:
        """Per-ticker ``(list_date, delist_date)`` (YYYYMMDD strings or None)
        from the already-pulled stock_basic parquets.

        Used by the freshness rule to bound what a year file can be expected
        to contain: a ticker listed after (or delisted before) the year slice
        legitimately has an empty placeholder, and a ticker delisted mid-year
        cannot have bars past its delist date. Missing/malformed columns
        degrade to ``(None, None)`` — the rule then expects the full slice,
        which only costs extra re-pulls, never skips real data."""
        windows: dict[str, tuple[str | None, str | None]] = {}
        for name in ("active_stocks.parquet", "delisted_stocks.parquet"):
            path = self._config.output_dir / name
            try:
                frame = pd.read_parquet(path)
            except Exception:  # noqa: BLE001 — universe loader already failed loud
                continue
            if "ts_code" not in frame.columns:
                continue
            for row in frame.itertuples(index=False):
                code = str(getattr(row, "ts_code", "") or "")
                if not code:
                    continue
                windows[code] = (
                    _clean_yyyymmdd(getattr(row, "list_date", None)),
                    _clean_yyyymmdd(getattr(row, "delist_date", None)),
                )
        return windows

    def _load_ticker_universe(self) -> tuple[str, ...]:
        """Return the union of active + delisted tickers from already-pulled
        stock_basic parquet files.

        Raises if stock_basic has not been pulled yet — the per-ticker loop
        cannot run without a ticker universe.
        """
        active_path = self._config.output_dir / "active_stocks.parquet"
        delisted_path = self._config.output_dir / "delisted_stocks.parquet"
        if not active_path.exists() or not delisted_path.exists():
            raise TushareFetcherError(
                "daily / adj_factor / daily_basic require "
                "active_stocks.parquet AND delisted_stocks.parquet to exist "
                "(run endpoint='stock_basic' first). Missing: "
                + ", ".join(p.name for p in (active_path, delisted_path)
                           if not p.exists())
            )
        active = pd.read_parquet(active_path)
        delisted = pd.read_parquet(delisted_path)
        tickers = sorted(set(active["ts_code"]) | set(delisted["ts_code"]))
        return tuple(tickers)

    def _safe_call(self, api_name: str, **params: Any) -> pd.DataFrame:
        """Call Tushare with per-call sleep + retry backoff.

        Retries are triggered for TWO error classes (see
        :meth:`_is_retryable_error` for the predicate):

        * **Rate limit**: Tushare quota window exhaustion. Recovery is
          quota-window-scale (~60s).
        * **Transient network error**: ``ConnectionError`` /
          ``ReadTimeout`` / 5xx gateway errors that the underlying
          ``requests`` stack surfaces through ``TushareClientError``.
          These typically resolve within seconds, but we use the
          same linear backoff (60s × attempt) because (a) network
          blips can persist longer than a single TCP retry-window
          and (b) one schedule keeps the operator-facing behaviour
          predictable.

        Re-raises non-retryable ``TushareClientError`` immediately so
        the operator sees true failures (missing token, malformed
        parameter, account-permission errors) without 5 minutes of
        misleading retries — these abort the whole run fast.

        On retryable EXHAUSTION (all ``MAX_RATE_LIMIT_RETRIES`` attempts
        failed) raises :class:`FetchHoleError` instead — the recoverable
        signal the per-endpoint loops turn into a recorded hole and
        continue past (P3-4a continue-on-error).
        """
        last_err: TushareClientError | None = None
        for attempt in range(MAX_RATE_LIMIT_RETRIES):
            try:
                if self._config.rate_limit_sleep_ms > 0:
                    time.sleep(self._config.rate_limit_sleep_ms / 1000.0)
                return self._client.call(api_name, **params)
            except TushareClientError as exc:
                if not self._is_retryable_error(exc):
                    # Token / permission / param error: re-raise so
                    # the operator gets the real error fast, not
                    # 5 attempts × 60s of misleading backoff.
                    raise
                last_err = exc
                # Only back off if another attempt remains. Sleeping after
                # the final attempt would waste a full backoff period
                # (currently up to ~300s) before raising — bad in the
                # per-ticker/year loops where exhaustion compounds (Codex
                # review #99 PR comment).
                if attempt < MAX_RATE_LIMIT_RETRIES - 1:
                    wait = RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1)
                    _logger.warning(
                        "  Transient Tushare error on %s attempt %d/%d, "
                        "sleeping %ds: %s",
                        api_name, attempt + 1, MAX_RATE_LIMIT_RETRIES, wait, exc,
                    )
                    time.sleep(wait)
                else:
                    _logger.warning(
                        "  Transient Tushare error on %s attempt %d/%d (final): %s",
                        api_name, attempt + 1, MAX_RATE_LIMIT_RETRIES, exc,
                    )
        # Retryable retries exhausted: a RECOVERABLE hole, not a hard abort.
        # The calling per-endpoint loop catches this, records the unit, and
        # continues (P3-4a). Non-retryable errors took the ``raise`` above and
        # abort fast.
        raise FetchHoleError(
            api_name,
            reason_class="transient",
            attempts=MAX_RATE_LIMIT_RETRIES,
            last_error=self._sanitize_error(last_err),
        )

    @staticmethod
    def _sanitize_error(exc: TushareClientError | None) -> str:
        """Bounded, token-free error string for a hole record.

        ``TushareClientError`` never carries the token (``client.py`` is the
        secrets boundary), so this only bounds the length so a verbose vendor
        error body cannot bloat the (P3-4b) manifest or a log line.
        """
        if exc is None:
            return ""
        return f"{type(exc).__name__}: {str(exc)[:300]}"

    def _add_hole(
        self, endpoint: str, unit: str, *,
        reason_class: str, attempts: int, last_error: str,
    ) -> None:
        """Append a :class:`FetchHole` and log it loudly so the operator sees it
        as it happens (the CLI also reports the full set + a non-zero exit at the
        end). Used both for a retry-exhausted call (via :meth:`_record_hole`) and
        for a unit skipped because a prerequisite (``stock_basic``) holed earlier
        in the same run."""
        self._holes.append(FetchHole(
            endpoint=endpoint, unit=unit, reason_class=reason_class,
            attempts=attempts, last_error=last_error,
        ))
        _logger.warning(
            "  HOLE: %s [%s] (%s, %d attempts) — continuing. %s",
            endpoint, unit, reason_class, attempts, last_error,
        )

    def _record_hole(self, endpoint: str, unit: str, err: FetchHoleError) -> None:
        """Record a hole for a unit whose call exhausted its retryable retries."""
        self._add_hole(
            endpoint, unit, reason_class=err.reason_class,
            attempts=err.attempts, last_error=err.last_error,
        )

    # Retry POLICY over the client's structured ``kind`` (P3-7). The client
    # states the classified FACT; this is the only place that decides which
    # kinds are worth retrying. ``auth`` / ``param`` / ``environment`` need
    # operator action, and ``unknown`` aborts fast by the P3-4a stance — an
    # unrecognized failure must not burn 5 × 60-300s of backoff per unit
    # across thousands of units before anyone notices.
    _RETRYABLE_KINDS = frozenset({KIND_RATE_LIMIT, KIND_NETWORK, KIND_SERVER_ERROR})

    @staticmethod
    def _is_retryable_error(exc: TushareClientError) -> bool:
        """True iff ``exc`` is a transient error worth retrying.

        PRIMARY: the structured ``kind`` the client stamped at wrap time
        (P3-7), classified from the RAW vendor failure before any wrapper
        prose existed. The previous message-substring approach was broken
        in production: ``client.call`` appended "Common causes: rate limit
        (account tier too low), missing parameter, or transient network
        error." to EVERY failure, so every error — including invalid
        token / missing permission / bad params — substring-matched as
        retryable and the fast-abort path below was unreachable; a wrong
        token ground through 5 × 60-300s of backoff per unit instead of
        aborting the run on the first call.

        FALLBACK (``kind is None`` only — a ``TushareClientError``
        constructed directly without classification, e.g. legacy call
        sites or tests): the original substring sets, unchanged:

        * **Rate limit**: ``"rate"`` / ``"limit"`` / ``returned none``.
        * **Transient network**: ``connection`` / ``timeout`` /
          ``max retries exceeded`` / ``http(s)connectionpool``.
        * **5xx gateway**: ``502`` / ``503`` / ``504`` / ``bad gateway`` /
          ``gateway time-out`` / ``service unavailable``.
        * **Tushare Chinese transients**: ``网络`` / ``服务异常`` /
          ``服务繁忙``.
        * Anything else (token / permission / param / unrecognized) is
          NOT retried — recovery requires operator action, not time.

        Static method so it can be unit-tested without a fetcher instance.
        """
        kind = getattr(exc, "kind", None)
        if kind is not None:
            return kind in TushareFetcher._RETRYABLE_KINDS
        msg = str(exc).lower()
        return any(
            token in msg
            for token in (
                # Rate-limit class — original tokens preserved.
                "rate", "limit", "returned none", "返回 none",
                # Transient network class (audit P0-followup; this
                # caught the user-reported ConnectionError on
                # api.waditu.com).
                "connection", "connectionerror", "connectionreseterror",
                "timeout", "timed out", "max retries exceeded",
                "httpconnectionpool", "httpsconnectionpool",
                # 5xx gateway.
                "502", "503", "504",
                "bad gateway", "gateway time-out", "service unavailable",
                # Chinese transient-error messages from Tushare server.
                "网络", "服务异常", "服务繁忙",
            )
        )

