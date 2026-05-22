"""CLI: Phase A.4 — resolve historical index membership.

Reads ``<tushare_dir>/index_weight/*.parquet`` written by Phase A.1
(``01_fetch_tushare.py --endpoints index_weight``) and emits
``<output_dir>/instruments/{csi300,csi500,csi800}.txt`` in qlib's
native tab-separated 3-column format.

Usage::

    python scripts/data_pipeline/03_resolve_index_membership.py \\
        --tushare-dir D:/qlib_data/tushare_raw \\
        --output-dir D:/qlib_data/my_cn_data_pit \\
        --reference-cases tests/pit/reference_cases.yaml

Optional ``--indices`` argument restricts the run to a subset of the 3
default indices (``000300.SH,000905.SH,000906.SH``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logger import get_logger, setup_logging  # noqa: E402
from src.data.pit.index_membership import (  # noqa: E402
    DEFAULT_INDEX_FILE_MAP,
    IndexMembershipError,
    IndexMembershipResolver,
)

# See Phase A.1 logger-namespace note: setup_logging only attaches a
# handler under src.*; using __name__ at script level would silently
# drop log output.
_logger = get_logger("src.scripts.data_pipeline.resolve_index_membership")


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Resolve historical index membership from Tushare "
                    "index_weight dumps (Phase A.4).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--tushare-dir", required=True, type=Path,
        help="Directory containing index_weight/*.parquet from Phase A.1.",
    )
    p.add_argument(
        "--output-dir", required=True, type=Path,
        help="Directory under which instruments/*.txt files are written.",
    )
    p.add_argument(
        "--reference-cases", type=Path, default=None,
        help="Optional path to reference_cases.yaml for validation.",
    )
    p.add_argument(
        "--indices",
        default=",".join(DEFAULT_INDEX_FILE_MAP.keys()),
        help="Comma-separated index codes. Default: "
             + ",".join(DEFAULT_INDEX_FILE_MAP.keys()),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = _build_arg_parser().parse_args(argv)
    indices = tuple(i.strip() for i in args.indices.split(",") if i.strip())

    try:
        resolver = IndexMembershipResolver(
            tushare_dir=args.tushare_dir,
            output_dir=args.output_dir,
            reference_cases_path=args.reference_cases,
            indices=indices,
        )
    except IndexMembershipError as exc:
        _logger.error("Config invalid: %s", exc)
        return 2

    try:
        results = resolver.resolve()
    except IndexMembershipError as exc:
        _logger.error("Resolve failed: %s", exc)
        return 1

    _logger.info("")
    _logger.info("=== Summary ===")
    for r in results:
        _logger.info(
            "  %s -> %s (runs=%d, tickers=%d, snapshots %s..%s, ref matched=%d)",
            r.index_code, r.output_path, r.run_count, r.distinct_tickers,
            r.earliest_snapshot, r.latest_snapshot, r.reference_rows_matched,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
