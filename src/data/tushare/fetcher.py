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

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from src.core.logger import get_logger
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

    The freshness rule's calendar floor: China A-share year ends trade on the
    last December weekday (no CN holiday occupies it), so a year file ending
    there is complete. Mid-year, a requested end falling on a weekend floors
    to Friday so a Friday-complete file is not pointlessly re-pulled all
    weekend. Intra-week CN holidays are NOT modelled — a run during one
    re-pulls final-year files and gets the same data back (bounded churn that
    converges as soon as the next bar lands), which errs on the side of
    re-fetching rather than skipping real data.
    """
    d = datetime.strptime(yyyymmdd, "%Y%m%d").date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def _expected_year_file_end(
    *,
    year_start: str,
    year_end: str,
    window: tuple[str | None, str | None],
) -> str | None:
    """Latest ``trade_date`` this run can expect inside a (ticker, year) file,
    or ``None`` when no data can exist for the slice.

    ``year_start`` / ``year_end`` are the run's CLIPPED slice for the year
    (already bounded by the config range). The ticker's listing ``window``
    further bounds it: listed-after-slice or delisted-before-slice ⇒ ``None``
    (an empty placeholder is the truthful content); delisted mid-slice caps
    the expectation at the delist date. The result is floored to the last
    weekday. Note the cap errs toward re-fetching: a ticker suspended through
    its slice end (or whose final bar precedes its delist date) yields a file
    that genuinely ends early and is re-pulled on every scan of that year —
    bounded waste, never a silent skip of real data.
    """
    list_date, delist_date = window
    lo = year_start
    hi = year_end
    if list_date is not None and list_date > lo:
        lo = list_date
    if delist_date is not None and delist_date < hi:
        hi = delist_date
    if lo > hi:
        return None
    floored = _last_weekday_str(hi)
    if floored < lo:
        return None
    return floored


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
            self._atomic_write_parquet(df, path)
            _logger.info("  wrote %d rows to %s", len(df), path)
            written += 1
            rows += len(df)
        return TushareFetchResult("stock_basic", written, rows, skipped)

    def _fetch_namechange(self) -> TushareFetchResult:
        """Pull all name changes in [start_date, end_date]. One call."""
        path = self._config.output_dir / "all_namechanges.parquet"
        if (path.exists() and not self._config.refresh_current
                and not self._must_retry("namechange", "file")):
            _logger.info("  skip (exists): %s", path)
            return TushareFetchResult("namechange", 0, 0, skipped=1)
        if self._config.dry_run:
            _logger.info("  [dry-run] would write %s", path)
            return TushareFetchResult("namechange", 0, 0, skipped=0)
        try:
            df = self._safe_call(
                "namechange",
                start_date=self._config.start_date,
                end_date=self._config.end_date,
                fields="ts_code,name,start_date,end_date,ann_date,change_reason",
            )
        except FetchHoleError as hole:
            # codex P2: namechange is a SINGLE file covering the run's range, so
            # the hole IS the whole file — the unit is a stable "file", NOT the
            # range (which varies run-to-run and would make a wider/narrower
            # re-failure look like a different unit, so the merge could not match
            # the prior hole and would reset attempts / drop it). The range lives
            # in the manifest's coverage fields, not the hole unit.
            self._record_hole("namechange", "file", hole)
            return TushareFetchResult("namechange", 0, 0, skipped=0)
        self._atomic_write_parquet(df, path)
        _logger.info("  wrote %d rows to %s", len(df), path)
        return TushareFetchResult("namechange", 1, len(df))

    def _fetch_suspend_d(self) -> TushareFetchResult:
        """Pull suspend / resume history in [start_date, end_date]."""
        path = self._config.output_dir / "suspend_d.parquet"
        if (path.exists() and not self._config.refresh_current
                and not self._must_retry("suspend_d", "file")):
            _logger.info("  skip (exists): %s", path)
            return TushareFetchResult("suspend_d", 0, 0, skipped=1)
        if self._config.dry_run:
            _logger.info("  [dry-run] would write %s", path)
            return TushareFetchResult("suspend_d", 0, 0, skipped=0)
        try:
            df = self._safe_call(
                "suspend_d",
                start_date=self._config.start_date,
                end_date=self._config.end_date,
                fields="ts_code,trade_date,suspend_timing,suspend_type",
            )
        except FetchHoleError as hole:
            # codex P2: like namechange, suspend_d is a single file — stable
            # "file" unit, not the run's range (see _fetch_namechange).
            self._record_hole("suspend_d", "file", hole)
            return TushareFetchResult("suspend_d", 0, 0, skipped=0)
        self._atomic_write_parquet(df, path)
        _logger.info("  wrote %d rows to %s", len(df), path)
        return TushareFetchResult("suspend_d", 1, len(df))

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
            # index_weight is exempt from refresh_current, but a prior-manifest
            # hole still forces a re-attempt (its file may be yesterday's).
            if path.exists() and not self._must_retry("index_weight", f"index={idx}"):
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
            self._atomic_write_parquet(df, path)
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
            year_floor = _last_weekday_str(min(self._config.end_date, f"{year}1231"))
            scan_year = (
                year == end_year
                or self._config.verify_all_years
                or watermark is None
                or year_floor > watermark[1]
                or year_start < watermark[0]
            )
            for i, ticker in enumerate(tickers, 1):
                path = year_dir / f"{ticker}.parquet"
                unit = f"ts_code={ticker} year={year}"
                # Set when this unit is a RE-PULL of an existing file (stale
                # by the freshness rule, OR force-retried off a prior-manifest
                # hole): the re-pulled frame is re-checked against the same
                # boundary after the write (codex P1 rounds 3+4).
                recheck_boundary: str | None = None
                force_retry = self._must_retry(endpoint, unit)
                if path.exists() and force_retry:
                    # codex P1 round 4: a force-retried EXISTING file bypasses
                    # the freshness branch below, but a successful retry that
                    # writes a still-short frame must surface in the aggregate
                    # warning too — its hole self-heals in the merge, and the
                    # warning is the remaining trace.
                    recheck_boundary = _expected_year_file_end(
                        year_start=year_start,
                        year_end=year_end,
                        window=windows.get(ticker, (None, None)),
                    )
                if path.exists() and not force_retry:
                    if not scan_year:
                        # Attested by the prior manifest's watermark — closed
                        # history this run cannot expect more from. A BLIND
                        # skip: proves nothing, establishes nothing.
                        skipped += 1
                        continue
                    expected = _expected_year_file_end(
                        year_start=year_start,
                        year_end=year_end,
                        window=windows.get(ticker, (None, None)),
                    )
                    if expected is None:
                        # The ticker's listing window misses this year slice —
                        # no data can exist, so a readable EMPTY placeholder is
                        # the truthful content: POSITIVE knowledge → verified
                        # (codex P2: must establish coverage, unlike a blind
                        # resume skip). But VERIFY the placeholder before
                        # claiming it (codex P2 round 2): a corrupt blob or a
                        # file holding unexpected rows (external mutation /
                        # interrupted write) falls through to the re-pull,
                        # which overwrites it with a clean empty placeholder.
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
                self._atomic_write_parquet(df, path)
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
                    new_max = str(df["trade_date"].max()) if len(df) else None
                    if new_max is None or new_max < recheck_boundary:
                        still_short.append(unit)
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
            ratio = len(still_short) / rechecked
            if (
                rechecked >= SYSTEMIC_SHORTFALL_MIN_CHECKED
                and ratio > SYSTEMIC_SHORTFALL_RATIO
            ):
                # SYSTEMIC: this is not a handful of suspended tickers — a
                # large share of fresh full-year re-pulls came back short of
                # the boundary that made their old files stale. The two real
                # shapes are a PRE-CLOSE run (today's bars not yet published —
                # the run must not pass as canonical) and vendor-side
                # truncation. Record an ENDPOINT hole so the run exits 3 and
                # the P3-4c build gate refuses the dump (round 5, 拍死选项1).
                self._add_hole(
                    endpoint,
                    "systemic-shortfall",
                    reason_class="systemic_shortfall",
                    attempts=1,
                    last_error=(
                        f"{len(still_short)}/{rechecked} re-checked re-pulls "
                        f"still end before their expected boundary "
                        f"(pre-close run before today's bars are published, "
                        f"or vendor-side truncation?). First: "
                        + "; ".join(still_short[:3])
                    )[:300],
                )
            else:
                _logger.warning(
                    "  %s: %d of %d re-checked re-pull(s) STILL end before "
                    "their expected boundary after a fresh full-year pull "
                    "(first: %s). Below the systemic threshold, this is the "
                    "vendor's complete answer — normal for tickers suspended "
                    "through a slice end or delisted before their delist "
                    "date; if neither applies, suspect silent vendor "
                    "truncation and re-run with --verify-all-years after "
                    "checking the source.",
                    endpoint, len(still_short), rechecked,
                    "; ".join(still_short[:3]),
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

    @staticmethod
    def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
        """Write parquet via temp file + rename so a killed process cannot
        leave a half-written file that would later be mis-skipped by
        existence-check resume."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        df.to_parquet(tmp_path, index=False)
        tmp_path.replace(path)
