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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.core.logger import get_logger
from src.data.tushare.client import TushareClient, TushareClientError

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

# Stock_basic field list for both 'L' and 'D' buckets. ts_code, list_date,
# delist_date are the load-bearing fields for Phase A.2; the rest are
# kept for diagnostics.
STOCK_BASIC_FIELDS = (
    "ts_code,symbol,name,area,industry,market,list_date,delist_date,"
    "list_status,curr_type"
)


class TushareFetcherError(RuntimeError):
    """Raised when the fetcher cannot continue (rate-limit exhaustion,
    unexpected vendor error, malformed config, etc.). Distinct from
    :class:`TushareClientError` (which is the per-call boundary) so
    callers can distinguish fetcher-orchestration failures from raw
    Tushare failures."""


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


@dataclass(frozen=True)
class TushareFetchResult:
    """Per-endpoint summary returned by :meth:`TushareFetcher.fetch`."""

    endpoint: str
    files_written: int
    rows_total: int
    skipped: int = 0


class TushareFetcher:
    """Orchestrator that pulls the Phase A.1 endpoints into a directory.

    Construction is cheap; ``fetch()`` does the work. Each endpoint method
    is also callable on its own for testing / incremental refresh.
    """

    def __init__(self, client: TushareClient, config: TushareFetcherConfig) -> None:
        self._client = client
        self._config = config

    # ------------------------------------------------------------------
    # Public orchestrator
    # ------------------------------------------------------------------

    def fetch(self) -> list[TushareFetchResult]:
        """Pull every endpoint in the configured set, in fixed order.

        Per-endpoint failures surface as :class:`TushareFetcherError`
        without retry beyond rate-limit backoff. Operator re-runs to
        resume from the failing endpoint (file-existence checkpoint
        means already-written files are skipped).
        """
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

    def _fetch_stock_basic(self) -> TushareFetchResult:
        """Pull both 'L' (active) and 'D' (delisted) buckets as separate files."""
        written = 0
        rows = 0
        skipped = 0
        for label, status in [("active_stocks", "L"), ("delisted_stocks", "D")]:
            path = self._config.output_dir / f"{label}.parquet"
            if path.exists():
                _logger.info("  skip (exists): %s", path)
                skipped += 1
                continue
            if self._config.dry_run:
                _logger.info("  [dry-run] would write %s (list_status=%r)", path, status)
                continue
            df = self._safe_call(
                "stock_basic",
                exchange="",
                list_status=status,
                fields=STOCK_BASIC_FIELDS,
            )
            self._atomic_write_parquet(df, path)
            _logger.info("  wrote %d rows to %s", len(df), path)
            written += 1
            rows += len(df)
        return TushareFetchResult("stock_basic", written, rows, skipped)

    def _fetch_namechange(self) -> TushareFetchResult:
        """Pull all name changes in [start_date, end_date]. One call."""
        path = self._config.output_dir / "all_namechanges.parquet"
        if path.exists():
            _logger.info("  skip (exists): %s", path)
            return TushareFetchResult("namechange", 0, 0, skipped=1)
        if self._config.dry_run:
            _logger.info("  [dry-run] would write %s", path)
            return TushareFetchResult("namechange", 0, 0, skipped=0)
        df = self._safe_call(
            "namechange",
            start_date=self._config.start_date,
            end_date=self._config.end_date,
            fields="ts_code,name,start_date,end_date,ann_date,change_reason",
        )
        self._atomic_write_parquet(df, path)
        _logger.info("  wrote %d rows to %s", len(df), path)
        return TushareFetchResult("namechange", 1, len(df))

    def _fetch_suspend_d(self) -> TushareFetchResult:
        """Pull suspend / resume history in [start_date, end_date]."""
        path = self._config.output_dir / "suspend_d.parquet"
        if path.exists():
            _logger.info("  skip (exists): %s", path)
            return TushareFetchResult("suspend_d", 0, 0, skipped=1)
        if self._config.dry_run:
            _logger.info("  [dry-run] would write %s", path)
            return TushareFetchResult("suspend_d", 0, 0, skipped=0)
        df = self._safe_call(
            "suspend_d",
            start_date=self._config.start_date,
            end_date=self._config.end_date,
            fields="ts_code,trade_date,suspend_timing,suspend_type",
        )
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
            if path.exists():
                _logger.info("  skip (exists): %s", path)
                skipped += 1
                continue
            if self._config.dry_run:
                _logger.info("  [dry-run] would write %s (chunked %d-%d)",
                             path, start_year, end_year)
                continue
            chunks: list[pd.DataFrame] = []
            for year in range(start_year, end_year + 1):
                y_start = f"{year}0101"
                y_end = f"{year}1231"
                if y_start < self._config.start_date:
                    y_start = self._config.start_date
                if y_end > self._config.end_date:
                    y_end = self._config.end_date
                chunk = self._safe_call(
                    "index_weight",
                    index_code=idx,
                    start_date=y_start,
                    end_date=y_end,
                    fields="index_code,con_code,trade_date,weight",
                )
                if not chunk.empty:
                    chunks.append(chunk)
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
        tickers = self._load_ticker_universe()
        out_root = self._config.output_dir / subdir
        start_year = int(self._config.start_date[:4])
        end_year = int(self._config.end_date[:4])
        written = 0
        rows = 0
        skipped = 0
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
            for i, ticker in enumerate(tickers, 1):
                path = year_dir / f"{ticker}.parquet"
                if path.exists():
                    skipped += 1
                    continue
                if self._config.dry_run:
                    if i == 1:
                        _logger.info(
                            "  [dry-run] would pull %s for %d tickers × year %d",
                            endpoint, len(tickers), year,
                        )
                    continue
                df = self._safe_call(
                    endpoint,
                    ts_code=ticker,
                    start_date=year_start,
                    end_date=year_end,
                    fields=fields,
                )
                if df.empty and not self._config.write_empty_placeholders:
                    continue
                self._atomic_write_parquet(df, path)
                written += 1
                rows += len(df)
                if i % 200 == 0:
                    _logger.info(
                        "  %s year=%d progress: %d/%d tickers (written=%d, skipped=%d)",
                        endpoint, year, i, len(tickers), written, skipped,
                    )
        return TushareFetchResult(endpoint, written, rows, skipped)

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
        misleading retries.
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
        raise TushareFetcherError(
            f"Tushare {api_name} failed {MAX_RATE_LIMIT_RETRIES} retryable "
            f"attempts (rate limit, network, or 5xx); aborting. "
            f"Last error: {last_err}"
        )

    @staticmethod
    def _is_retryable_error(exc: TushareClientError) -> bool:
        """True iff ``exc`` is a transient error worth retrying.

        Covered classes:

        * **Rate limit**: ``"rate"`` / ``"limit"`` substrings;
          Tushare's quota exhaustion sometimes surfaces as
          ``returned None`` (no body in the SDK).
        * **Transient network**: ``ConnectionError`` /
          ``ConnectionResetError`` (catches the user-reported
          ``HTTPConnectionPool(host='api.waditu.com')`` blip),
          ``Timeout`` / ``timed out``, ``max retries exceeded``
          (raised by the requests adapter when its OWN retry
          budget is exhausted on transport-level failures).
        * **5xx gateway**: ``502``, ``503``, ``504``, ``bad gateway``,
          ``gateway time-out``, ``service unavailable``.
        * **Tushare Chinese error messages**: ``网络`` (network),
          ``服务异常`` (service abnormal), ``服务繁忙`` (server busy).
          Tushare's pro API sometimes returns Chinese error bodies
          on transient failures; matching the substrings here means
          a misconfigured operator locale doesn't accidentally lose
          the retry. Static method so it can be unit-tested without
          a fetcher instance.

        NOT retried:

        * Token / authentication errors (``"token"``, ``"权限"``,
          ``"invalid"``) — recovery requires operator action, not
          time.
        * Param errors (``"missing"``, ``"required"``) — same.
        * Anything not in the substring set above.
        """
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
