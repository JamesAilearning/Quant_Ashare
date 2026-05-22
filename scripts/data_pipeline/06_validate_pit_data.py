"""CLI: Phase B.3 + B.4 — validate the qlib provider built by Phase B.2.

Runs the 6-check PIT validation suite (per design §5 Stage 6):

  A. Survivorship spot-check
  B. Delist boundary sweep (full registry)
  C. Time-travel sanity
  D. qlib operator min_periods at delist boundary (§4.3.2)
  E. Index membership references
  F. Borrow-shell continuity

Usage::

    python scripts/data_pipeline/06_validate_pit_data.py \\
        --provider-dir D:/qlib_data/my_cn_data_pit \\
        --delisted-registry D:/qlib_data/tushare_raw/delisted_registry.parquet \\
        --reference-cases tests/pit/reference_cases.yaml \\
        --report-json /tmp/pit_validation.json

Exit codes (per legacy verify_survivorship.py convention):
  0 = all checks pass cleanly
  1 = warnings only (e.g. reference YAML deferred)
  2 = any failure
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logger import get_logger, setup_logging  # noqa: E402
from src.data.pit.pit_validator import (  # noqa: E402
    PITValidator,
    PITValidatorError,
)

_logger = get_logger("src.scripts.data_pipeline.validate_pit_data")


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run the PIT validation suite against a built provider "
                    "(Phase B.3 + B.4).",
    )
    p.add_argument("--provider-dir", required=True, type=Path)
    p.add_argument("--delisted-registry", required=True, type=Path)
    p.add_argument("--reference-cases", type=Path, default=None)
    p.add_argument(
        "--report-json", type=Path, default=None,
        help="Optional path to write the structured validation report.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = _build_arg_parser().parse_args(argv)
    validator = PITValidator(
        provider_dir=args.provider_dir,
        delisted_registry_path=args.delisted_registry,
        reference_cases_path=args.reference_cases,
    )
    try:
        report = validator.validate()
    except PITValidatorError as exc:
        _logger.error("Validation setup failed: %s", exc)
        return 2

    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _logger.info("Wrote structured report to %s", args.report_json)

    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
