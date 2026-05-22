"""CLI: Phase A.2 — build the delisted-stock registry from Tushare dumps.

Reads ``stock_basic`` parquet dumps written by Phase A.1
(``01_fetch_tushare.py``) and the user-curated reference cases YAML
(``tests/pit/reference_cases.yaml``), emits ``delisted_registry.parquet``
under the configured output directory, and asserts:

- Every ``pure_delisting_cases`` / ``batch_delisting_cases`` reference
  row is present with matching ``delist_date``.
- No ``active_control_cases`` ticker appears in the delisted registry.

Usage::

    python scripts/data_pipeline/02_build_delisted_registry.py \\
        --tushare-dir D:/qlib_data/tushare_raw \\
        --reference-cases tests/pit/reference_cases.yaml \\
        --output D:/qlib_data/tushare_raw/delisted_registry.parquet

Failure modes
-------------
- Tushare dump missing -> exit 1 with explicit "run Phase A.1 first"
- Reference row not in registry -> exit 1 with diff
- Active control in registry -> exit 1 (false positive)
- delist_date unparseable -> exit 1 (Tushare schema drift)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logger import get_logger, setup_logging  # noqa: E402
from src.data.pit.delisted_registry import (  # noqa: E402
    DelistedRegistryBuilder,
    DelistedRegistryError,
)

# See Phase A.1 logger-namespace note: setup_logging only attaches a
# handler under src.*; using __name__ at script level would silently
# drop log output.
_logger = get_logger("src.scripts.data_pipeline.build_delisted_registry")


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Build the delisted-stock registry from Tushare "
                    "dumps + reference cases YAML (Phase A.2).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--tushare-dir", required=True, type=Path,
        help="Directory containing active_stocks.parquet + "
             "delisted_stocks.parquet from Phase A.1.",
    )
    p.add_argument(
        "--reference-cases", required=True, type=Path,
        help="Path to tests/pit/reference_cases.yaml.",
    )
    p.add_argument(
        "--output", required=True, type=Path,
        help="Output path for delisted_registry.parquet.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = _build_arg_parser().parse_args(argv)

    builder = DelistedRegistryBuilder(
        tushare_dir=args.tushare_dir,
        reference_cases_path=args.reference_cases,
        output_path=args.output,
    )

    try:
        result = builder.build()
    except DelistedRegistryError as exc:
        _logger.error("Registry build failed: %s", exc)
        return 1

    _logger.info("")
    _logger.info("=== Summary ===")
    _logger.info("  output:                    %s", result.output_path)
    _logger.info("  rows:                      %d", result.row_count)
    _logger.info("  reference rows matched:    %d", result.reference_rows_matched)
    _logger.info("  active controls checked:   %d", result.active_controls_checked)
    return 0


if __name__ == "__main__":
    sys.exit(main())
