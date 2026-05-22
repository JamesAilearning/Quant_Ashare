"""CLI: Phase A.1 — fetch raw Tushare data for A-share survivorship correction.

Reads ``TUSHARE_TOKEN`` from the environment via
:class:`TushareClient.from_environment`. The token MUST NOT appear in
any CLI argument, config file, or log line.

Usage::

    # Smoke test: pull stock_basic only, dry-run
    python scripts/data_pipeline/01_fetch_tushare.py \\
        --endpoints stock_basic --dry-run \\
        --output-dir /tmp/tushare_dry

    # Real fetch of stock_basic + namechange (fast — 3 calls total)
    python scripts/data_pipeline/01_fetch_tushare.py \\
        --endpoints stock_basic,namechange \\
        --output-dir D:/qlib_data/tushare_raw

    # Full backfill (long pole — 12-24h at 5000-point tier)
    python scripts/data_pipeline/01_fetch_tushare.py \\
        --output-dir D:/qlib_data/tushare_raw

Resume
------
The script uses per-file existence as its checkpoint. Re-running with
the same ``--output-dir`` skips any file already on disk. No separate
``.checkpoint`` file is maintained — if you need to force a re-pull of
a specific endpoint, delete the corresponding output file(s) before
re-running.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logger import get_logger, setup_logging  # noqa: E402
from src.data.tushare.client import TushareClient, TushareClientError  # noqa: E402
from src.data.tushare.fetcher import (  # noqa: E402
    DEFAULT_INDICES,
    DEFAULT_RATE_LIMIT_SLEEP_MS,
    ENDPOINTS,
    TushareFetcher,
    TushareFetcherConfig,
    TushareFetcherError,
)

# `setup_logging` only attaches a handler to the ``src.*`` logger
# namespace; using ``__name__`` here (which resolves to ``__main__`` when
# the script is run via ``python scripts/...``) would silently drop log
# output. Pin under ``src.scripts.*`` so handlers attach correctly.
_logger = get_logger("src.scripts.data_pipeline.fetch_tushare")


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Fetch raw Tushare data for the A-share survivorship "
                    "correction pipeline (Phase A.1).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--output-dir", required=True, type=Path,
        help="Directory to write parquet dumps into. Existing files are "
             "skipped (resume semantics).",
    )
    p.add_argument(
        "--start-date", default="20000101",
        help="YYYYMMDD inclusive (default: 20000101).",
    )
    p.add_argument(
        "--end-date", default="20251231",
        help="YYYYMMDD inclusive (default: 20251231).",
    )
    p.add_argument(
        "--endpoints", default=",".join(ENDPOINTS),
        help=f"Comma-separated endpoint names. Default: all 7. Valid: {','.join(ENDPOINTS)}",
    )
    p.add_argument(
        "--indices", default=",".join(DEFAULT_INDICES),
        help=f"Comma-separated index codes for index_weight. Default: {','.join(DEFAULT_INDICES)}",
    )
    p.add_argument(
        "--rate-limit-sleep-ms", type=int, default=DEFAULT_RATE_LIMIT_SLEEP_MS,
        help=f"Sleep (ms) between Tushare calls. Default {DEFAULT_RATE_LIMIT_SLEEP_MS}ms "
             "= 300 calls/min; tune for your account tier.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Do not write any files; log what would happen.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = _build_arg_parser().parse_args(argv)

    endpoints = tuple(e.strip() for e in args.endpoints.split(",") if e.strip())
    indices = tuple(i.strip() for i in args.indices.split(",") if i.strip())

    try:
        config = TushareFetcherConfig(
            output_dir=args.output_dir,
            start_date=args.start_date,
            end_date=args.end_date,
            endpoints=endpoints,
            indices=indices,
            rate_limit_sleep_ms=args.rate_limit_sleep_ms,
            dry_run=args.dry_run,
        )
    except TushareFetcherError as exc:
        _logger.error("Config invalid: %s", exc)
        return 2

    try:
        client = TushareClient.from_environment()
    except TushareClientError as exc:
        _logger.error("Cannot construct Tushare client: %s", exc)
        return 1

    fetcher = TushareFetcher(client, config)
    try:
        results = fetcher.fetch()
    except (TushareFetcherError, TushareClientError) as exc:
        _logger.error("Fetch failed: %s", exc)
        return 1

    _logger.info("")
    _logger.info("=== Summary ===")
    total_written = 0
    total_rows = 0
    total_skipped = 0
    for r in results:
        _logger.info(
            "  %-14s  files_written=%5d  rows=%10d  skipped=%5d",
            r.endpoint, r.files_written, r.rows_total, r.skipped,
        )
        total_written += r.files_written
        total_rows += r.rows_total
        total_skipped += r.skipped
    _logger.info("  %-14s  files_written=%5d  rows=%10d  skipped=%5d",
                 "TOTAL", total_written, total_rows, total_skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
