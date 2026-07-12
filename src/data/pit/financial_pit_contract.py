"""Financial-statement PIT contract layer (阶段8 Gate-2, PR-1).

Turns the versioned raw store (``src.data.tushare.financial_statements``) into
PIT-contract-keyed records. This is the data-layer contract — deterministic
metadata about WHEN each filing became knowable. It computes NO factor and
applies NO carry-forward / exclusion / exposure (those are the PR-2
``FinancialPITDataView``'s job).

Contract fields (spec ``v2-financial-pit-contract``)
----------------------------------------------------
* ``report_period`` — the quarter the record describes (``end_date``).
* ``announcement_date`` — ``f_ann_date``, falling back to ``ann_date`` with the
  fallback RECORDED in ``announcement_date_source``. If BOTH are absent the
  record is UNAVAILABLE — it is NEVER assigned an availability date derived from
  the report period.
* ``available_from_trade_date`` — the first trading day STRICTLY AFTER the
  announcement (post-close assumption), from the canonical bundle calendar. All
  PIT joins key on this; the report-period end is never an availability date.
* revision linkage — a revised (``update_flag=1``) record carries the content
  hash of the as-originally-reported (``update_flag=0``) record for the same
  ``(ts_code, report_period)``, so a consumer can serve original-first.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd

from src.data.trading_calendar import StaticTradingCalendar
from src.data.tushare.financial_statements import (
    COL_CONTENT_HASH,
    COL_FETCH_BATCH,
    LOGICAL_KEY,
)

# Contract output columns.
REPORT_PERIOD = "report_period"
ANNOUNCEMENT_DATE = "announcement_date"
ANNOUNCEMENT_SOURCE = "announcement_date_source"  # "f_ann_date" | "ann_date" | ""
AVAILABLE_FROM = "available_from_trade_date"
REVISION_OF = "revision_of_content_hash"          # set on update_flag=1 rows

# Provenance columns (_content_hash / _fetch_batch) are REQUIRED too: revision
# linkage + latest-batch resolution are part of the contract, so a
# provenance-stripped frame must fail loud rather than silently drop restatement
# lineage (codex #340 r5 P2).
_REQUIRED = (
    "ts_code", "end_date", "ann_date", "f_ann_date", "update_flag",
    COL_CONTENT_HASH, COL_FETCH_BATCH,
)


class FinancialPITContractError(RuntimeError):
    """Raised when the contract cannot be derived honestly (missing columns,
    malformed dates). Fail-loud — never a silently-defaulted availability date."""


def _parse_yyyymmdd(value: object) -> date | None:
    """Parse a tushare ``YYYYMMDD`` value (str or int) to a date.

    Returns ``None`` ONLY for a true blank / NA (legitimate missingness). A
    NON-blank token that is not a valid ``YYYYMMDD`` (wrong length, non-digit,
    or an impossible date like ``20221301``) RAISES
    :class:`FinancialPITContractError` — malformed input is corruption, and
    hiding it behind "missing" would silently drop a filing's availability or
    fall back to a wrong announcement date (codex #340 P2)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    token = str(value).strip()
    if not token or token in {"None", "nan", "NaT", "<NA>"}:
        return None
    # Tolerate ONLY an exact ".0" float coercion ("20220331.0" from a pandas
    # float column); a NON-ZERO fraction ("20220331.5") is a malformed token,
    # not a date, and must NOT be truncated into a valid-looking date that would
    # go PIT-visible on the wrong day (codex #340 r4 P2).
    if "." in token:
        int_part, _, frac = token.partition(".")
        digits = int_part if frac.strip("0") == "" else ""
    else:
        digits = token
    if len(digits) == 8 and digits.isdigit():
        try:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
        except ValueError:
            pass  # right shape, impossible calendar date -> malformed, fall through
    raise FinancialPITContractError(
        f"malformed date token {value!r} (expected YYYYMMDD or a blank/NA) — "
        "refusing to hide corruption behind 'missing'."
    )


def _parse_date_column(series: pd.Series, colname: str) -> pd.Series:
    """Map ``_parse_yyyymmdd`` over a column, naming the column if a malformed
    token raises (so the fail-loud message points at the corrupt input)."""
    try:
        return series.map(_parse_yyyymmdd)
    except FinancialPITContractError as exc:
        raise FinancialPITContractError(f"column {colname!r}: {exc}") from exc


def build_contract_frame(
    store: pd.DataFrame, calendar: StaticTradingCalendar,
) -> pd.DataFrame:
    """Augment a raw-store frame with the PIT contract fields.

    Input is rows as written by ``FinancialStatementIngestor`` (may span many
    instruments / batches). Output adds ``report_period``, ``announcement_date``,
    ``announcement_date_source``, ``available_from_trade_date`` and
    ``revision_of_content_hash``. Rows with no announcement date get NaT
    availability (UNAVAILABLE) — never a period-end fallback.
    """
    missing = [c for c in _REQUIRED if c not in store.columns]
    if missing:
        raise FinancialPITContractError(
            f"store frame missing required columns {missing}; "
            f"have {sorted(store.columns)}."
        )
    out = store.copy()
    report_period = _parse_date_column(out["end_date"], "end_date")
    f_ann = _parse_date_column(out["f_ann_date"], "f_ann_date")
    ann = _parse_date_column(out["ann_date"], "ann_date")

    announcement: list[date | None] = []
    source: list[str] = []
    for fa, a in zip(f_ann, ann, strict=True):
        if fa is not None:
            announcement.append(fa)
            source.append("f_ann_date")
        elif a is not None:
            announcement.append(a)
            source.append("ann_date")
        else:
            # BOTH absent — unavailable. NEVER derive availability from the
            # report period (that would be a look-ahead to the quarter end).
            announcement.append(None)
            source.append("")

    available = [
        calendar.next_trading_day_after(d) if d is not None else None
        for d in announcement
    ]

    out[REPORT_PERIOD] = pd.Series(report_period, index=out.index, dtype="object")
    out[ANNOUNCEMENT_DATE] = pd.Series(announcement, index=out.index, dtype="object")
    out[ANNOUNCEMENT_SOURCE] = pd.Series(source, index=out.index, dtype="object")
    out[AVAILABLE_FROM] = pd.Series(available, index=out.index, dtype="object")
    out[REVISION_OF] = _revision_linkage(out)
    return out


def _revision_linkage(frame: pd.DataFrame) -> pd.Series:
    """For each ``update_flag=1`` row, the content hash of the as-originally-
    reported (``update_flag=0``) row for the same ``(ts_code, report_period)``
    (latest batch if several). NA for original rows / when no original exists."""
    # _content_hash / _fetch_batch are guaranteed present (build_contract_frame
    # validates _REQUIRED first), so latest-batch resolution of the original is
    # unconditional — no silent NA fallback on stripped provenance.
    link: dict[tuple[str, object], str] = {}
    originals = frame[frame["update_flag"].astype(str) == "0"]
    for (ts, rp), grp in originals.groupby(["ts_code", REPORT_PERIOD], dropna=False):
        grp = grp.sort_values(COL_FETCH_BATCH)  # latest batch wins if re-fetched
        link[(str(ts), rp)] = str(grp.iloc[-1][COL_CONTENT_HASH])
    out: list[object] = []
    for _, row in frame.iterrows():
        if str(row["update_flag"]) == "1":
            out.append(link.get((str(row["ts_code"]), row[REPORT_PERIOD]), pd.NA))
        else:
            out.append(pd.NA)
    return pd.Series(out, index=frame.index, dtype="object")


def resolve_current_versions(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep the LATEST batch per logical key ``(ts_code, end_date, update_flag)``.

    The physical store is append-only; this is the read-time resolution the
    spec mandates — the newest fetch of a logical record wins, but every prior
    version stays in the store (a changed re-fetch is retained, not lost).

    Fail-loud (codex #340 P2): a frame missing ``_fetch_batch`` or any member of
    the logical key cannot be resolved to a single current version. Returning it
    unresolved would expose MULTIPLE physical versions as current and leak
    superseded financial rows into PIT use — so this refuses rather than passing
    an unresolvable frame through."""
    required = (COL_FETCH_BATCH, *LOGICAL_KEY)
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise FinancialPITContractError(
            f"cannot resolve current versions: frame missing {missing} — "
            "latest-batch resolution needs the fetch batch and the logical key "
            f"{LOGICAL_KEY}; refusing to return an unresolved frame (it would "
            "expose superseded versions as current)."
        )
    ordered = frame.sort_values(COL_FETCH_BATCH)
    return ordered.drop_duplicates(
        subset=list(LOGICAL_KEY), keep="last",
    ).reset_index(drop=True)


def _assert_resolved(frame: pd.DataFrame, where: str) -> None:
    """Fail loud if ``frame`` still has MULTIPLE physical rows per logical version
    ``(ts_code, report_period, update_flag)`` — it was not passed through
    :func:`resolve_current_versions`, so the append-only store's superseded
    batches would be treated as current and skew the collapse / residual audit
    (codex #345 P2). Callers must resolve current versions first."""
    key = ["ts_code", REPORT_PERIOD, "update_flag"]
    if frame.duplicated(subset=key).any():
        raise FinancialPITContractError(
            f"{where}: frame has duplicate logical versions {key} — "
            "resolve_current_versions() must run first so superseded append-only "
            "batches are not treated as current."
        )


def _assert_binary_update_flag(frame: pd.DataFrame, where: str) -> None:
    """Fail loud on any ``update_flag`` value that is not exactly ``0`` or ``1``.

    The ingest only ever writes 0/1, but ``build_contract_frame`` does not
    re-validate; a legacy/corrupt store row (``update_flag='2'`` or blank) would
    be ranked exactly like a revision and could be served as the disclosure of
    record when the period has no ``update_flag=0`` — a silent contract
    violation. Refuse rather than rank an unknown flag (codex #345 r2)."""
    flags = {str(u) for u in frame["update_flag"].unique()}
    bad = sorted(flags - {"0", "1"})
    if bad:
        raise FinancialPITContractError(
            f"{where}: update_flag has non-0/1 value(s) {bad} — the ingest only "
            "writes 0/1; a legacy/corrupt store must be re-ingested, not silently "
            "ranked as a revision."
        )


def select_disclosure_of_record(frame: pd.DataFrame) -> pd.DataFrame:
    """Collapse each ``(ts_code, report_period)`` to its DISCLOSURE OF RECORD.

    For each period, keep the ``update_flag=0`` (as-originally-reported) row when
    the period has one; when the period has NO ``update_flag=0`` row, keep the
    sole ``update_flag=1`` row — which IS that period's original disclosure of
    record. The provider does not always retain an ``update_flag=0`` row for the
    most recent 1–2 years of periods, and DISCARDING a ``update_flag=1``-only
    period (the old ``update_flag=0`` FILTER did) staled the served value by 1–2
    years — a correctness bug, not honest PIT (spec ``v2-financial-pit-contract``,
    阶段8 Gate-2 correction).

    A both-version period ALWAYS resolves to ``update_flag=0`` — a restated value
    is never served over its original. PIT safety is therefore STRUCTURAL (only a
    period's first/sole disclosure is ever kept), independent of whether
    ``update_flag=0`` and ``=1`` values coincide across the universe.
    """
    if frame.empty:
        return frame.copy()
    for col in ("ts_code", "update_flag", REPORT_PERIOD):
        if col not in frame.columns:
            raise FinancialPITContractError(
                f"cannot select disclosure-of-record: frame missing {col!r}."
            )
    _assert_resolved(frame, "select_disclosure_of_record")
    _assert_binary_update_flag(frame, "select_disclosure_of_record")
    work = frame.copy()
    # prefer update_flag=0 (rank 0) over any revision (rank 1) within a period.
    work["_uf_rank"] = work["update_flag"].astype(str).map(
        lambda u: 0 if u == "0" else 1,
    )
    ordered = work.sort_values(["ts_code", REPORT_PERIOD, "_uf_rank"])
    picked = ordered.drop_duplicates(
        subset=["ts_code", REPORT_PERIOD], keep="first",
    )
    return picked.drop(columns=["_uf_rank"]).reset_index(drop=True)


@dataclass(frozen=True)
class VersionCollapseResidual:
    """The audited restatement residual of the version-collapse honesty envelope.

    ``per_field`` maps a charter field -> ``(n_compared, n_differ)`` over the
    both-version periods where both rows have a non-NA value; ``differing`` lists
    the ``(ts_code, report_period, field)`` genuine restatements."""

    n_both_version_periods: int
    per_field: dict[str, tuple[int, int]]
    differing: list[tuple[str, object, str]]

    def overall_differing_fraction(self) -> float:
        tot = sum(compared for compared, _ in self.per_field.values())
        dif = sum(differ for _, differ in self.per_field.values())
        return (dif / tot) if tot else 0.0


def version_collapse_residual(
    frame: pd.DataFrame, fields: Sequence[str],
) -> VersionCollapseResidual:
    """Audit the version-collapse residual: across every ``(ts_code,
    report_period)`` that has BOTH an ``update_flag=0`` and an ``update_flag=1``
    row, measure the fraction whose value DIFFERS (a genuine restatement) versus
    is EQUAL (a version marker only), per charter ``field``.

    This SIZES the restatement residual for the honesty envelope; it is NOT a
    safety precondition — :func:`select_disclosure_of_record` always resolves a
    differing both-version period to ``update_flag=0``, so a non-zero residual
    introduces NO look-ahead (spec ``v2-financial-pit-contract`` version-collapse
    audit requirement)."""
    per_field: dict[str, tuple[int, int]] = {f: (0, 0) for f in fields}
    differing: list[tuple[str, object, str]] = []
    if frame.empty:
        return VersionCollapseResidual(0, per_field, differing)
    missing = [f for f in fields if f not in frame.columns]
    if missing:
        raise FinancialPITContractError(
            f"version-collapse audit: frame missing field column(s) {missing}."
        )
    for col in ("ts_code", "update_flag", REPORT_PERIOD):
        if col not in frame.columns:
            raise FinancialPITContractError(
                f"version-collapse audit: frame missing {col!r}."
            )
    _assert_resolved(frame, "version-collapse audit")
    _assert_binary_update_flag(frame, "version-collapse audit")
    n_both = 0
    for (ts, rp), grp in frame.groupby(["ts_code", REPORT_PERIOD], dropna=False):
        rows = {str(r["update_flag"]): r for _, r in grp.iterrows()}
        if "0" not in rows or "1" not in rows:
            continue
        n_both += 1
        r0, r1 = rows["0"], rows["1"]
        for f in fields:
            v0, v1 = r0[f], r1[f]
            if pd.notna(v0) and pd.notna(v1):
                compared, differ = per_field[f]
                is_diff = bool(v0 != v1)
                per_field[f] = (compared + 1, differ + (1 if is_diff else 0))
                if is_diff:
                    differing.append((str(ts), rp, f))
    return VersionCollapseResidual(n_both, per_field, differing)
