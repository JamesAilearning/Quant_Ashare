"""Independent synthetic schedule/context literals; never access live data."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CN_TZ = timezone(timedelta(hours=8))


def calendar_context(day="2025-06-30", *, fetched_at=None):
    """A writer-only provenance fixture, not a claimed fetched calendar."""
    decision = date.fromisoformat(day)
    return {
        "path": "synthetic_recommendation_calendar.json",
        "sha256": "c" * 64,
        "source_api": "trade_cal",
        "exchange": "SSE",
        "query": {"start_date": (decision - timedelta(days=30)).strftime("%Y%m%d"),
                  "end_date": (decision + timedelta(days=30)).strftime("%Y%m%d"),
                  "fields": "exchange,cal_date,is_open"},
        "fetched_at": fetched_at or f"{day}T16:00:00+08:00",
    }


def write_calendar(path: Path, *, now: datetime, start_date: str,
                   end_date: str, closed_dates=(), open_dates=None):
    """Write a complete *synthetic* daily SSE arrangement to a tmp path.

    Weekdays here are fixture inputs, not a production fallback. Explicit
    holiday/open-day overrides let tests distinguish arranged sessions from
    weekday arithmetic and from the shorter recorded-price calendar.
    """
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    rows = []
    current = start
    while current <= end:
        iso = current.isoformat()
        is_open = (iso in open_dates if open_dates is not None
                   else current.weekday() < 5 and iso not in closed_dates)
        rows.append({"exchange": "SSE", "cal_date": current.strftime("%Y%m%d"),
                     "is_open": int(is_open)})
        current += timedelta(days=1)
    payload = {
        "schema_version": 1, "source_api": "trade_cal", "exchange": "SSE",
        "query": {"start_date": start.strftime("%Y%m%d"),
                  "end_date": end.strftime("%Y%m%d"),
                  "fields": "exchange,cal_date,is_open"},
        "fetched_at": now.isoformat(), "rows": rows,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def read_calendar(path: Path, *, now: datetime, start_date: str,
                  end_date: str, closed_dates=(), open_dates=None):
    from src.data.recommendation_calendar import read_snapshot

    write_calendar(path, now=now, start_date=start_date, end_date=end_date,
                   closed_dates=closed_dates, open_dates=open_dates)
    return read_snapshot(path, now=now)
