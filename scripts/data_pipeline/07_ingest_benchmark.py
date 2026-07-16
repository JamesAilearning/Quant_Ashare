"""CLI: Phase B.5 — fetch + ingest benchmark INDEX series into the bundle (PR-E).

Fetches each configured benchmark index's daily series from tushare
``index_daily`` and writes it into ``--provider-dir`` as a qlib instrument
(``src.data.pit.benchmark_index_ingest``). Run AFTER 05 (the bin builder)
against the SAME staging dir the rebuild promotes, so the atomic swap
preserves the benchmark — the retired ``scripts/ingest_sh000300_benchmark.py``
wrote bins into the LIVE bundle post hoc and the swap erased them, and the
series it carried was the CSI 300 PRICE index (audit E2: benchmarking
dividend-inclusive strategy returns against a price index overstates excess
return by ~the index dividend yield).

Two indices are ingested by default: the CSI 300 PRICE index
(``000300.SH`` -> ``SH000300``, kept for reference) and its TOTAL-RETURN
twin (``H00300.CSI`` -> ``SH000300TR``, the canonical benchmark once the
config default is switched). The total-return index publishes CLOSE ONLY;
the ingest module fills OHLC from close.

Reads ``TUSHARE_TOKEN`` from the environment via
``TushareClient.from_environment``. The token MUST NOT appear in any CLI
argument, config file, or log line.

Usage::

    python scripts/data_pipeline/07_ingest_benchmark.py \\
        --provider-dir D:/qlib_data/my_cn_data_pit \\
        --start-date 20180101 --end-date 20251231
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

from src.core.logger import get_logger, setup_logging  # noqa: E402
from src.data.pit.benchmark_index_ingest import (  # noqa: E402
    BenchmarkIngestError,
    PreparedBenchmark,
    commit_prepared,
    prepare_benchmark_index,
)
from src.data.tushare.client import TushareClient, TushareClientError  # noqa: E402

_logger = get_logger("src.scripts.data_pipeline.ingest_benchmark")

# tushare index code -> qlib instrument name. Every PER-UNIVERSE canonical
# total-return benchmark (src/core/backtest_runner.py
# ``_CANONICAL_BENCHMARK_BY_UNIVERSE``) MUST be listed here: the orchestrated
# daily rebuild invokes this stage with no --index-map into a FRESH staging
# bundle, so a canonical code missing from this default silently vanishes at
# the next atomic swap and every run consuming it fails at backtest time
# (codex P1 on #365). Guarded by
# tests/governance/test_canonical_benchmark_default_consistency.py.
# The price index is kept for reference / REGEN-A control.
DEFAULT_INDEX_MAP: tuple[tuple[str, str], ...] = (
    ("000300.SH", "SH000300"),     # CSI 300 price index (REGEN-A control)
    ("H00300.CSI", "SH000300TR"),  # CSI 300 total-return (canonical: csi300/all)
    ("H00906.CSI", "SH000906TR"),  # CSI 800 total-return (canonical: csi800)
    ("H00905.CSI", "SH000905TR"),  # CSI 500 total-return (canonical: csi500)
)

_INDEX_DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,vol"


def _fetch_index_daily(
    client: TushareClient, ts_code: str, start_date: str, end_date: str,
) -> pd.DataFrame:
    """Pull one index's daily series. Fail loud on an empty frame — a
    benchmark we cannot fetch must stop the run, not write zero rows."""
    df = client.call(
        "index_daily",
        ts_code=ts_code,
        start_date=start_date,
        end_date=end_date,
        fields=_INDEX_DAILY_FIELDS,
    )
    if df is None or len(df) == 0:
        raise BenchmarkIngestError(
            f"index_daily returned no rows for {ts_code} in "
            f"[{start_date}, {end_date}] — refusing to ingest an empty "
            "benchmark. Check the index code and the account's index "
            "permissions."
        )
    return df


def _parse_index_map(raw: str | None) -> tuple[tuple[str, str], ...]:
    if not raw:
        return DEFAULT_INDEX_MAP
    pairs: list[tuple[str, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            raise ValueError(
                f"--index-map item {item!r} must be 'TUSHARE_CODE:QLIB_NAME'."
            )
        ts_code, qlib_name = (s.strip() for s in item.split(":", 1))
        # Reject an empty side BEFORE any provider file is touched (codex P2
        # on #243): an empty qlib name would write bins straight under
        # features/ and a blank-code row into benchmark.txt; an empty
        # tushare code would fetch nothing.
        if not ts_code or not qlib_name:
            raise ValueError(
                f"--index-map item {item!r} has an empty TUSHARE_CODE or "
                "QLIB_NAME; both sides are required."
            )
        pairs.append((ts_code, qlib_name))
    if not pairs:
        raise ValueError("--index-map resolved to no pairs.")
    return tuple(pairs)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Fetch + ingest benchmark index series into a qlib bundle "
                    "(Phase B.5 / PR-E).",
    )
    p.add_argument(
        "--provider-dir", required=True, type=Path,
        help="Bundle dir to write into (pass the STAGING dir during a "
             "rebuild so the atomic swap preserves the benchmark).",
    )
    p.add_argument("--start-date", default="20180101", help="YYYYMMDD inclusive.")
    p.add_argument(
        "--end-date", default=None,
        help="YYYYMMDD inclusive. Default: today's date (matches the "
             "daily-update orchestrator, which passes the run date). A "
             "hardcoded end would stop the benchmark short of a bundle whose "
             "calendar extends past it, leaving 2026+ backtests with missing "
             "benchmark rows.",
    )
    p.add_argument(
        "--index-map", default=None,
        help="Comma-separated TUSHARE_CODE:QLIB_NAME pairs; default "
             + ",".join(f"{a}:{b}" for a, b in DEFAULT_INDEX_MAP),
    )
    p.add_argument(
        "--best-effort", default="H00300.CSI,H00906.CSI,H00905.CSI",
        help="Comma-separated tushare index codes whose fetch/permission "
             "failure DOWNGRADES to a warning+skip instead of failing the "
             "run. Default: the H*.CSI total-return indices — their index "
             "entitlement is often separate from the equity endpoints, so a "
             "manual/standalone run should not hard-fail on a missing one. "
             "The ORCHESTRATED daily rebuild passes an empty string instead: "
             "every index (all per-universe canonical benchmarks included) "
             "is mandatory there, because a fresh staging bundle missing a "
             "canonical code fails every run that consumes it at backtest "
             "time. Pass an empty string to make every index mandatory.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = _build_arg_parser().parse_args(argv)
    try:
        index_map = _parse_index_map(args.index_map)
    except ValueError as exc:
        _logger.error("Config invalid: %s", exc)
        return 2

    # Default the end date to today (matches the daily-update orchestrator,
    # which passes the run date) so a standalone run never stops the
    # benchmark short of a bundle whose calendar extends past a stale literal.
    end_date = args.end_date or date.today().strftime("%Y%m%d")
    best_effort = {c.strip() for c in args.best_effort.split(",") if c.strip()}

    try:
        client = TushareClient.from_environment()
    except TushareClientError as exc:
        _logger.error("Cannot construct Tushare client: %s", exc)
        return 1

    # Two phases (codex P2 on #243): PREPARE every index (fetch + validate +
    # compute bins IN MEMORY) before COMMITTING any. A later index's fetch
    # or transform failure then aborts the run BEFORE the provider is touched
    # — no mixed live benchmark state where the price index updated but the
    # total-return one did not. Best-effort downgrades only FETCH-class
    # failures (a separate index entitlement the daily swap must tolerate);
    # a transform/contract failure after a successful fetch is always fatal.
    prepared: list[PreparedBenchmark] = []
    for ts_code, qlib_name in index_map:
        _logger.info("=== benchmark index: %s -> %s ===", ts_code, qlib_name)
        try:
            frame = _fetch_index_daily(
                client, ts_code, args.start_date, end_date,
            )
        except (TushareClientError, BenchmarkIngestError) as exc:
            if ts_code in best_effort:
                _logger.warning(
                    "Benchmark FETCH skipped for best-effort index %s "
                    "(%s); continuing. The canonical benchmark (price index) "
                    "is mandatory and must still succeed.", ts_code, exc,
                )
                continue
            _logger.error("Benchmark fetch FAILED for %s: %s", ts_code, exc)
            return 1
        try:
            prepared.append(prepare_benchmark_index(
                frame, instrument_code=qlib_name, provider_dir=args.provider_dir,
            ))
        except (BenchmarkIngestError, OSError) as exc:
            # A fetched-but-malformed source (or a calendar read failure) is
            # always fatal, never best-effort — and it aborts BEFORE any
            # provider write, so nothing is half-written.
            _logger.error(
                "Benchmark PREPARE FAILED for %s (NOT downgraded to "
                "best-effort — a malformed source must not silently ship a "
                "price-only benchmark; nothing written): %s", ts_code, exc,
            )
            return 1

    if not prepared:
        _logger.error(
            "No benchmark index prepared — every configured index failed "
            "(all were best-effort?). Refusing a silent no-op.",
        )
        return 1

    # All required indices validated; commit them. A write failure here
    # (disk full / permission) is rare and maps to a stage exit; the ingest
    # is idempotent, so a re-run rewrites cleanly.
    for item in prepared:
        try:
            result = commit_prepared(item, provider_dir=args.provider_dir)
        except OSError as exc:
            _logger.error(
                "Benchmark WRITE FAILED for %s after validation: %s",
                item.instrument_code, exc,
            )
            return 1
        _logger.info(
            "  ingested %s: %s..%s (%d trading days, %d gap day(s) "
            "forward-filled, ohlc_degenerate=%s)",
            result.instrument_code, result.first_date, result.last_date,
            result.n_trading_days, result.n_gap_days, result.ohlc_degenerate,
        )
    _logger.info(
        "Benchmark ingest complete (%d of %d index/indices).",
        len(prepared), len(index_map),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
