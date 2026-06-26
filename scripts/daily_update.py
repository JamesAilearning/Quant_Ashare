"""Single-entry daily data update (P3-6a).

Fetch (refresh-current) → snapshot check → full rebuild into <provider>.new →
validate → atomic swap. Each stage is fail-loud and short-circuits the rest;
exit codes identify the failing stage (see src/data_pipeline/daily_update.py).

Example
-------
    python scripts/daily_update.py \\
        --tushare-dir D:/qlib_data/tushare_raw \\
        --provider-dir D:/qlib_data/my_cn_data_pit \\
        --delisted-registry D:/qlib_data/tushare_raw/delisted_registry.parquet \\
        --reference-cases tests/pit/reference_cases.yaml

    # See the plan without touching anything:
    python scripts/daily_update.py ... --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logger import setup_logging  # noqa: E402
from src.data_pipeline.daily_update import (  # noqa: E402
    EXIT_ALREADY_RUNNING,
    EXIT_CONFIG,
    DailyUpdateConfig,
    run_daily_update,
)
from src.data_pipeline.single_flight import (  # noqa: E402
    AlreadyRunningError,
    SingleFlightSetupError,
    single_flight,
)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Daily data update: fetch -> snapshot -> rebuild -> "
                    "validate -> atomic swap (P3-6a).",
    )
    p.add_argument("--tushare-dir", required=True, type=Path,
                   help="Raw tushare dump directory (01's --output-dir).")
    p.add_argument("--provider-dir", required=True, type=Path,
                   help="LIVE qlib provider dir; the rebuild stages into "
                        "<provider-dir>.new and swaps only after validation.")
    p.add_argument("--delisted-registry", required=True, type=Path,
                   help="delisted_registry.parquet path (02 writes, 04/05/06 read).")
    p.add_argument("--reference-cases", required=True, type=Path,
                   help="tests/pit/reference_cases.yaml (02 requires; 03/06 validate).")
    p.add_argument("--start-date", default="20180101",
                   help="Fetch range start, YYYYMMDD (default 20180101 — the "
                        "2018+ bundle start; the bins build has no range filter, "
                        "so fetching pre-2018 years widens the built calendar). "
                        "Pass an earlier date only for a deliberate full-history "
                        "build.")
    p.add_argument("--end-date", default=None,
                   help="Fetch range end, YYYYMMDD (default: today).")
    p.add_argument("--rate-limit-sleep-ms", type=int, default=None,
                   help="Passed through to 01 (default: 01's own default).")
    p.add_argument("--allow-holey-fetch", action="store_true",
                   help="Continue past fetch holes and build a partial bundle "
                        "stamped built-from-holey-fetch (P3-4c). Build-side "
                        "ONLY: the recommend boundary still refuses the bundle "
                        "unless --allow-holey-recommend is passed THERE.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print every stage's plan and the bundle state; "
                        "execute nothing, mutate nothing.")
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = _build_arg_parser().parse_args(argv)
    try:
        config = DailyUpdateConfig(
            tushare_dir=args.tushare_dir,
            provider_dir=args.provider_dir,
            delisted_registry=args.delisted_registry,
            reference_cases=args.reference_cases,
            start_date=args.start_date,
            end_date=args.end_date,
            allow_holey_fetch=args.allow_holey_fetch,
            dry_run=args.dry_run,
            rate_limit_sleep_ms=args.rate_limit_sleep_ms,
        )
    except (TypeError, ValueError) as exc:
        print(f"Config invalid: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    # Single-flight (阶段5 PR-P): a scheduled firing and a manual run (or a hung run
    # and the next day's firing) targeting the SAME provider must NOT overlap — the
    # swap is crash-atomic but not run-concurrent. A --dry-run mutates nothing, so it
    # is exempt (an operator can preview while a real run holds the lock).
    if config.dry_run:
        return run_daily_update(config)
    try:
        with single_flight(config.provider_dir):
            return run_daily_update(config)
    except AlreadyRunningError as exc:
        print(f"daily_update: {exc}", file=sys.stderr)
        return EXIT_ALREADY_RUNNING
    except SingleFlightSetupError as exc:
        # Unwritable / unreachable lock path is a setup problem, not contention — map it
        # to the config exit code rather than crashing with an undefined one.
        print(f"daily_update: {exc}", file=sys.stderr)
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
