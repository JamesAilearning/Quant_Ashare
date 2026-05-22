"""CLI: Phase B.1 — build qlib instruments/all.txt.

Reads Phase A.1's ``active_stocks.parquet`` and Phase A.2's
``delisted_registry.parquet``; emits ``<output_dir>/instruments/all.txt``
in qlib's tab-separated 3-column format.

Usage::

    python scripts/data_pipeline/04_build_universe_files.py \\
        --tushare-dir D:/qlib_data/tushare_raw \\
        --delisted-registry D:/qlib_data/tushare_raw/delisted_registry.parquet \\
        --output-dir D:/qlib_data/my_cn_data_pit
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logger import get_logger, setup_logging  # noqa: E402
from src.data.pit.universe_files import (  # noqa: E402
    UniverseFilesBuilder,
    UniverseFilesError,
)

_logger = get_logger("src.scripts.data_pipeline.build_universe_files")


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Build qlib instruments/all.txt from delisted registry "
                    "+ active stocks (Phase B.1).",
    )
    p.add_argument("--tushare-dir", required=True, type=Path)
    p.add_argument("--delisted-registry", required=True, type=Path)
    p.add_argument("--output-dir", required=True, type=Path)
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = _build_arg_parser().parse_args(argv)
    builder = UniverseFilesBuilder(
        tushare_dir=args.tushare_dir,
        delisted_registry_path=args.delisted_registry,
        output_dir=args.output_dir,
    )
    try:
        result = builder.build()
    except UniverseFilesError as exc:
        _logger.error("Universe file build failed: %s", exc)
        return 1
    _logger.info("")
    _logger.info("=== Summary ===")
    _logger.info("  output:    %s", result.output_path)
    _logger.info("  active:    %d", result.active_count)
    _logger.info("  delisted:  %d", result.delisted_count)
    _logger.info("  total:     %d", result.total_rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
