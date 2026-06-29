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

# The qlib instruments "open run" end-date sentinel: an active (or
# still-listed-at-bundle-end) ticker's run is written with this far-future end
# so qlib treats the run as open. Defined ONCE here — the PIT layer's
# shared-constant home — so the literal ``"2099-12-31"`` lives in one place;
# ``universe_files`` / ``index_membership`` re-export it and ``qlib_bin_builder``
# imports it.
QLIB_OPEN_END_DATE = "2099-12-31"


def to_qlib_ticker(ts_code: str) -> str:
    """Normalise a Tushare ``ts_code`` to qlib's instrument format.

    Tushare's ``stock_basic`` returns ``ts_code`` as ``<6-digit>.<exchange>``
    (e.g. ``600087.SH``, ``000023.SZ``). The rest of this project (design
    doc §4.1 example, ``tests/pit/reference_cases.yaml``, qlib's
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


def qlib_to_ts_code(code: str) -> str:
    """Inverse of :func:`to_qlib_ticker`: ``SH600000`` -> ``600000.SH``.

    Maps a qlib-style instrument (``<exchange><6-digit>``, e.g. ``SH600000``,
    ``SZ000001``, ``BJ832317``) back to the Tushare ``ts_code`` form
    (``<6-digit>.<exchange>``). Shared by the daily-recommendation ST filter
    (PR1) and the backtest historical-ST mask (PR2) so neither carries a
    private copy of the conversion.

    A value that already contains ``.`` (already ts-style) or whose shape is
    not ``<2-letter><digits>`` is returned unchanged — defer the failure to a
    validation step rather than silently mangling it (mirrors
    :func:`to_qlib_ticker`).
    """
    if "." in code:
        return code
    if len(code) > 2 and code[:2].isalpha():
        return f"{code[2:]}.{code[:2].upper()}"
    return code


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


__all__ = [
    "QLIB_OPEN_END_DATE",
    "qlib_to_ts_code",
    "to_iso_date",
    "to_qlib_ticker",
]
