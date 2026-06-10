"""CLI: Phase B.2 — build qlib bin storage from Tushare dumps.

Reads Phase A.1's ``daily/`` and ``adj_factor/`` parquets, Phase A.2's
``delisted_registry.parquet``, and Phase A.1's ``active_stocks.parquet``;
writes a complete qlib provider directory at ``--output-dir`` with
``calendars/day.txt`` + ``features/<ticker>/<field>.day.bin``.

Delisted tickers are NaN-padded past their ``delist_date`` per design
§4.3. Active tickers extend through the latest available trading day.
The provider is written via atomic rename — qlib never sees a partially
constructed directory mid-run.

Usage::

    python scripts/data_pipeline/05_build_qlib_bins.py \\
        --tushare-dir D:/qlib_data/tushare_raw \\
        --delisted-registry D:/qlib_data/tushare_raw/delisted_registry.parquet \\
        --output-dir D:/qlib_data/my_cn_data_pit

WARNING: The output-dir contents are REPLACED atomically. Existing
provider data at that path is briefly retained as a ``.<name>.bak``
sibling and removed on success. Specify a fresh path for the first
build.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logger import get_logger, setup_logging  # noqa: E402
from src.data.pit.qlib_bin_builder import (  # noqa: E402
    QlibBinBuilder,
    QlibBinBuilderError,
)

_logger = get_logger("src.scripts.data_pipeline.build_qlib_bins")


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Build qlib bin storage with NaN-after-delist (Phase B.2).",
    )
    p.add_argument("--tushare-dir", required=True, type=Path)
    p.add_argument("--delisted-registry", required=True, type=Path)
    p.add_argument("--output-dir", required=True, type=Path)
    p.add_argument(
        "--allow-holey-fetch", action="store_true",
        help="Build even if the tushare fetch_manifest is holey/missing (P3-4c). "
             "Produces a research/inspection bundle stamped built-from-holey-fetch; "
             "it is STILL refused at the recommend boundary unless "
             "--allow-holey-recommend is passed there separately.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = _build_arg_parser().parse_args(argv)
    builder = QlibBinBuilder(
        tushare_dir=args.tushare_dir,
        delisted_registry_path=args.delisted_registry,
        output_dir=args.output_dir,
        allow_holey_fetch=args.allow_holey_fetch,
    )
    try:
        result = builder.build()
    except QlibBinBuilderError as exc:
        _logger.error("Bin build failed: %s", exc)
        return 1
    _logger.info("")
    _logger.info("=== Summary ===")
    _logger.info("  provider_dir:           %s", result.output_dir)
    _logger.info("  calendar days:          %d", result.calendar_days)
    _logger.info("  tickers written:        %d", result.ticker_count)
    _logger.info("    of which delisted:    %d", result.delisted_ticker_count)
    _logger.info("  skipped (no data):      %d", result.skipped_no_data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
