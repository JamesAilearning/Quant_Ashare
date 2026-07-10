"""CLI: 阶段8 Gate-2 — versioned ingest of financial statements (research-only).

Fetches tushare ``income`` / ``balancesheet`` / ``cashflow`` for a universe of
instruments into the VERSION-PRESERVING raw store
(``src.data.tushare.financial_statements``). Research-only: computes NO factor,
feeds ONLY the (PR-2) ``FinancialPITDataView`` — never the canonical runtime.

Reads ``TUSHARE_TOKEN`` from the environment via ``TushareClient.from_environment``;
the token MUST NOT appear in any CLI argument, config file, or log line.

Universe: pass a qlib instruments file (default the CSI300 PIT membership, which
includes delisted names) — its tickers are translated to tushare ``ts_code``.

Usage::

    python scripts/data_pipeline/08_fetch_financials.py \\
        --store-dir D:/qlib_data/financial_pit_raw \\
        --instruments-file D:/qlib_data/my_cn_data_pit/instruments/csi300.txt
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logger import get_logger, setup_logging  # noqa: E402
from src.data.tushare.client import TushareClient, TushareClientError  # noqa: E402
from src.data.tushare.financial_statements import (  # noqa: E402
    FINANCIAL_ENDPOINTS,
    FinancialIngestError,
    FinancialStatementIngestor,
)

_logger = get_logger("src.scripts.data_pipeline.fetch_financials")


def _tushare_codes_from_instruments(path: Path) -> list[str]:
    """qlib instruments file (``SHxxxxxx``/``SZxxxxxx`` tab rows) -> tushare
    ``xxxxxx.SH``/``.SZ`` ts_codes, de-duplicated (membership spans repeat)."""
    codes: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if not parts or not parts[0]:
            continue
        m = re.search(r"(\d{6})", parts[0])
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        exchange = "SH" if parts[0].upper().startswith("SH") else "SZ"
        codes.append(f"{m.group(1)}.{exchange}")
    return codes


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Versioned financial-statement ingest (research-only).",
    )
    p.add_argument("--store-dir", required=True, type=Path,
                   help="Raw versioned store root.")
    p.add_argument("--instruments-file", type=Path, default=None,
                   help="qlib instruments file (e.g. csi300.txt) for the universe.")
    p.add_argument("--ts-codes", default=None,
                   help="Comma-separated tushare ts_codes; overrides --instruments-file.")
    p.add_argument("--endpoints", default=",".join(FINANCIAL_ENDPOINTS),
                   help=f"Comma-separated; default {','.join(FINANCIAL_ENDPOINTS)}.")
    p.add_argument("--rate-limit-sleep-ms", type=int, default=150,
                   help="Sleep between calls (ms).")
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = _build_arg_parser().parse_args(argv)

    if args.ts_codes:
        codes = [c.strip() for c in args.ts_codes.split(",") if c.strip()]
    elif args.instruments_file:
        if not args.instruments_file.is_file():
            _logger.error("instruments file not found: %s", args.instruments_file)
            return 2
        codes = _tushare_codes_from_instruments(args.instruments_file)
    else:
        _logger.error("pass --ts-codes or --instruments-file.")
        return 2
    if not codes:
        _logger.error("no ts_codes resolved.")
        return 2

    endpoints = tuple(e.strip() for e in args.endpoints.split(",") if e.strip())
    bad = [e for e in endpoints if e not in FINANCIAL_ENDPOINTS]
    if bad:
        _logger.error("unknown endpoint(s) %s; valid %s", bad, FINANCIAL_ENDPOINTS)
        return 2

    try:
        client = TushareClient.from_environment()
    except TushareClientError as exc:
        _logger.error("cannot construct Tushare client: %s", exc)
        return 1

    # fetch_batch stamps every row's provenance so a later re-fetch is a NEW
    # batch, never an in-place overwrite (append-only store contract).
    fetch_batch = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ingestor = FinancialStatementIngestor(client, args.store_dir)
    sleep_s = max(0, args.rate_limit_sleep_ms) / 1000.0
    _logger.info("ingest batch=%s  universe=%d  endpoints=%s",
                 fetch_batch, len(codes), endpoints)

    totals = {ep: [0, 0, 0] for ep in endpoints}  # rows_new, rows_changed, holes
    for i, ts_code in enumerate(codes):
        for endpoint in endpoints:
            try:
                res = ingestor.ingest(endpoint, ts_code, fetch_batch=fetch_batch)
                totals[endpoint][0] += res.rows_new
                totals[endpoint][1] += res.rows_changed
            except (FinancialIngestError, TushareClientError) as exc:
                totals[endpoint][2] += 1
                _logger.warning("  %s %s: %s", endpoint, ts_code, str(exc)[:120])
            time.sleep(sleep_s)
        if (i + 1) % 25 == 0:
            _logger.info("  ...%d/%d instruments", i + 1, len(codes))

    _logger.info("=== Summary (batch %s) ===", fetch_batch)
    holes = 0
    for ep in endpoints:
        new, changed, ep_holes = totals[ep]
        holes += ep_holes
        _logger.info("  %-13s rows_new=%d changed=%d holes=%d", ep, new, changed, ep_holes)
    # A holey ingest MUST NOT be mistaken for complete (mirrors 01's exit 3).
    return 3 if holes else 0


if __name__ == "__main__":
    sys.exit(main())
