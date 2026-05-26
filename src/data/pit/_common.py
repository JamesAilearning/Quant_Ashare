"""Shared ticker and date helpers for the PIT data layer.

Until now, ``_to_qlib_ticker`` was duplicated across
``delisted_registry``, ``index_membership``, ``universe_files``, and
``qlib_bin_builder`` — each carrying an identical implementation plus
a "consolidate later" TODO. ``_to_iso_date`` lived in three of those
four files. This module is that consolidation. (bug.md P2-4.)

Both helpers are pure stdlib — no qlib import, no pandas, no I/O —
so importing this module is cheap and safe from anywhere in the PIT
layer.
"""

from __future__ import annotations


def to_qlib_ticker(ts_code: str) -> str:
    """Normalise a Tushare ``ts_code`` to qlib's instrument format.

    Tushare's ``stock_basic`` returns ``ts_code`` as ``<6-digit>.<exchange>``
    (e.g. ``600087.SH``, ``000023.SZ``). The rest of this project (design
    doc §4.1 example, ``verify_survivorship.py::KNOWN_DELISTED``, qlib's
    ``instruments/*.txt`` convention) uses ``<exchange><6-digit>`` (e.g.
    ``SH600087``). PIT writers must emit the qlib-style form so
    ``reference_cases.yaml`` and downstream consumers match without
    per-call translation.

    Values without a dot are returned unchanged (already qlib-style or
    of unrecognised shape — defer the failure to the validation step
    instead of silently mangling them).
    """
    if "." not in ts_code:
        return ts_code
    code, exchange = ts_code.split(".", 1)
    if (
        len(code) == 6
        and code.isdigit()
        and len(exchange) == 2
        and exchange.isalpha()
    ):
        return f"{exchange.upper()}{code}"
    return ts_code


def to_iso_date(yyyymmdd: str) -> str:
    """``"20220630"`` → ``"2022-06-30"``.

    Strict input validation: anything that isn't an 8-digit numeric
    string raises ``ValueError`` rather than emitting a malformed
    ISO date that would later parse-fail somewhere deep in pandas.
    """
    s = str(yyyymmdd)
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f"expected YYYYMMDD, got {yyyymmdd!r}")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


__all__ = ["to_iso_date", "to_qlib_ticker"]
