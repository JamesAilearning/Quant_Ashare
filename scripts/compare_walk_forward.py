"""CLI for diffing two walk-forward runs.

Usage:
    python scripts/compare_walk_forward.py BASELINE_REPORT VARIANT_REPORT [--out PATH]

``BASELINE_REPORT`` and ``VARIANT_REPORT`` are paths to two
``walk_forward_report.json`` files produced by ``WalkForwardEngine.run``.
Prints a formatted side-by-side diff at INFO level; if ``--out`` is
given, also writes a JSON snapshot of the comparison.

Mirrors :mod:`scripts/run_walk_forward.py` in style — single
``main()`` with ``setup_logging()``, no third-party CLI parser; the
script stays small and obvious.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow ``python scripts/compare_walk_forward.py`` from the repo root —
# ensure the project root is on sys.path before importing src.*
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logger import get_logger, setup_logging  # noqa: E402
from src.core.walk_forward_compare import (  # noqa: E402
    compare_reports,
    print_comparison,
    write_comparison,
)

_logger = get_logger(__name__)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diff two walk-forward reports.",
    )
    parser.add_argument(
        "baseline",
        help="Path to the baseline walk_forward_report.json",
    )
    parser.add_argument(
        "variant",
        help="Path to the variant walk_forward_report.json",
    )
    parser.add_argument(
        "--out",
        default=None,
        help=(
            "Optional path to also write the comparison as JSON. "
            "Without --out the script only logs to stdout."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    setup_logging()
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    _logger.info(
        "Comparing walk-forward reports:\n  baseline: %s\n  variant:  %s",
        args.baseline, args.variant,
    )
    comparison = compare_reports(args.baseline, args.variant)
    print_comparison(comparison)

    if args.out:
        write_comparison(comparison, args.out)
        _logger.info("Comparison JSON written to %s", args.out)


if __name__ == "__main__":
    main()
