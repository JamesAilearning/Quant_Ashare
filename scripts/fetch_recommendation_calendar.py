"""Fetch one immutable SSE arrangement snapshot, never future price data."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.recommendation_timing import CN_TZ  # noqa: E402
from src.data.recommendation_calendar import (  # noqa: E402
    CalendarSnapshotError,
    fetch_snapshot,
    validate_query,
    write_snapshot,
)
from src.data.tushare.client import TushareClient, TushareClientError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", required=True, help="Inclusive YYYYMMDD")
    parser.add_argument("--end-date", required=True, help="Inclusive YYYYMMDD")
    parser.add_argument("--output", required=True, type=Path, help="New JSON path; existing paths refuse")
    args = parser.parse_args(argv)
    try:
        validate_query(args.start_date, args.end_date)
        client = TushareClient.from_environment()
        snapshot = fetch_snapshot(
            client, start_date=args.start_date, end_date=args.end_date, now=datetime.now(CN_TZ),
        )
        pin = write_snapshot(args.output, snapshot)
    except (CalendarSnapshotError, TushareClientError, OSError) as exc:
        # Vendor exception messages can contain credentials or HTTP details.
        # Do not echo arbitrary exception text, token values or SDK payloads.
        print(f"Trading-calendar acquisition failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(json.dumps({
        "path": str(args.output.absolute()), **pin, "source_api": "trade_cal", "exchange": "SSE",
        "query": snapshot["query"], "fetched_at": snapshot["fetched_at"], "scope": "session_arrangement_only",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
