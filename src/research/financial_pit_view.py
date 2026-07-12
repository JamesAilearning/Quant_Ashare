"""FinancialPITDataView — the SOLE research-side access path to PIT financial
features (阶段8 Gate-2 PR-2).

Research-only. Consumes the versioned raw store + PIT contract (PR-1) and serves
point-in-time-correct financial-statement values for factor research. It
computes NO factor (the GPA/PROF/OP formulas, C2's interest-term choice and C3's
accrual set are Gate-3). It is **isolated from the canonical runtime**: a
governance gate (``tests/governance/test_financial_pit_view_isolation.py``)
enforces that no canonical feature-registry / training / ``daily_recommend``
module imports this — the same machine-enforced boundary the D5 gate gives
``src/factor_mining/``.

Contract (spec ``v2-financial-pit-contract``)
---------------------------------------------
* **As-of carry-forward, not imputation** — for a query trade date, each
  instrument gets the value of its latest ALREADY-ANNOUNCED statement (held
  forward until a newer one is announced), NA where none has been announced.
* **Disclosure-of-record, original-first** — per report_period the
  ``update_flag=0`` value, or the sole ``update_flag=1`` when the provider kept
  no original (阶段8 Gate-2 correction: a uf1-only recent period is genuine
  data, not to be dropped); a both-version period ALWAYS resolves to
  ``update_flag=0`` so a restatement never overrides its original; undatable
  restatements are never back-applied (PR-1 contract).
* **Availability** keys on ``available_from_trade_date`` (first trading day
  strictly after the announcement) — a filing is invisible before it.
* **Missing stays missing** — NA, never 0 / cross-sectional median / latest /
  future.
* **Financial-sector exclusion** — issuers on a stable industry list are
  dropped from the research universe; ``oper_cost`` absence is the cross-check,
  reported (not silently resolved) on disagreement.
* Exposes the charter input columns raw, incl. BOTH ``adv_receipts`` AND
  ``contract_liab`` (the 2020 预收→合同负债 reclassification is documented; the
  coalesce is Gate-3 factor logic).
* **Known PIT limitation** — the provider assigns NO independent date to a
  restatement, and for recent periods retains only ``update_flag=1``. The one
  residual the data cannot rule out is a recent uf1-only period that silently
  corrects a first-announced value the provider no longer stores; it is bounded
  by the version-collapse audit's measured restatement rate (Gate-1 memo §3;
  ``financial_pit_contract.version_collapse_residual``).
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.data.pit._common import qlib_to_ts_code
from src.data.pit.financial_pit_contract import (
    AVAILABLE_FROM,
    REPORT_PERIOD,
    build_contract_frame,
    resolve_current_versions,
    select_disclosure_of_record,
)
from src.data.trading_calendar import StaticTradingCalendar
from src.data.tushare.financial_statements import DATA_FIELDS

# Tushare ts_code — the financial store's native key (``600000.SH.parquet``).
# The canonical universe is qlib-style (``SH600000``); we normalize inbound
# instruments to this shape before any store lookup / exclusion (codex #342).
_TS_CODE_RE = re.compile(r"^\d{6}\.[A-Z]{2}$")

# field -> its endpoint (revenue -> income, total_assets -> balancesheet, ...).
_FIELD_ENDPOINT: dict[str, str] = {
    f: endpoint for endpoint, fields in DATA_FIELDS.items() for f in fields
}

# The stable financial-sector industry list (banks/brokers/insurers). No PIT
# industry data exists, so this is a current snapshot — acceptable because
# sector membership is near-static (spec).
FINANCIAL_INDUSTRIES: frozenset[str] = frozenset(
    {"银行", "证券", "保险", "多元金融", "信托", "金融"}
)

# The line the financial-exclusion cross-check keys on: banks/brokers do not
# report 营业成本, so a financial issuer's oper_cost should be absent.
_CROSS_CHECK_FIELD = "oper_cost"


class FinancialPITViewError(RuntimeError):
    """Raised when the view cannot serve a query honestly (unknown field,
    unreadable store). Fail-loud — never a defaulted or imputed value."""


def _require_collection(value: object, name: str) -> None:
    """Reject a scalar ``str`` for a sequence parameter (codex #342 r9): a str
    satisfies ``Sequence[str]`` but iterates into CHARACTERS, so a caller who
    passes one ticker/field would silently query per-character garbage."""
    if isinstance(value, str):
        raise FinancialPITViewError(
            f"{name} must be a COLLECTION (list/tuple/…), not a single string "
            f"({value!r}) — a str iterates into characters. Wrap it in a list."
        )


@dataclass(frozen=True)
class ExclusionDisagreement:
    """A financial-exclusion cross-check mismatch, reported not resolved."""

    ts_code: str
    kind: str  # "financial_has_oper_cost" | "nonfinancial_never_reports_oper_cost"


def financial_issuers_from_industry(
    stock_basic: pd.DataFrame,
    *,
    financial_industries: Iterable[str] = FINANCIAL_INDUSTRIES,
) -> frozenset[str]:
    """Derive the financial-sector exclusion set (ts_codes) from a stock_basic
    snapshot's ``industry`` column. This is the stable industry list — the
    primary exclusion rule; ``oper_cost`` absence is only the cross-check."""
    if "ts_code" not in stock_basic.columns or "industry" not in stock_basic.columns:
        raise FinancialPITViewError(
            "stock_basic needs 'ts_code' and 'industry' columns to derive the "
            "financial-sector exclusion list."
        )
    wanted = set(financial_industries)
    mask = stock_basic["industry"].astype(str).isin(wanted)
    return frozenset(stock_basic.loc[mask, "ts_code"].astype(str))


class FinancialPITDataView:
    """Sole research-side PIT accessor for financial statements.

    Injectable ``calendar`` + ``store_dir`` so tests run without real data.
    ``financial_issuers`` is REQUIRED (the signed financial-sector exclusion) —
    there is NO default, so a forgotten list can never silently include
    banks/brokers/insurers and violate the universe contract (codex #342 r3).
    Derive it with :func:`financial_issuers_from_industry`, or pass an explicit
    ``frozenset()`` to deliberately disable exclusion (a visible choice).
    """

    def __init__(
        self,
        store_dir: str | Path,
        calendar: StaticTradingCalendar,
        *,
        financial_issuers: Iterable[str],
    ) -> None:
        self._store_dir = Path(store_dir)
        self._calendar = calendar
        # A single str satisfies Iterable[str] but would iterate into CHARACTERS
        # ({'0','.','S',...}), silently emptying the exclusion of real ts_codes
        # so banks slip in — reject it (codex #342 r7).
        if isinstance(financial_issuers, str):
            raise FinancialPITViewError(
                "financial_issuers must be a COLLECTION of ts_codes, not a single "
                f"string ({financial_issuers!r}) — a str iterates into characters. "
                "Pass a list/set/frozenset (or frozenset() to disable exclusion)."
            )
        # normalize the exclusion set to ts_code too, so a qlib-style OR ts_code
        # exclusion list matches the ts_code-normalized instruments (codex #342).
        self._financial = frozenset(
            qlib_to_ts_code(str(t)) for t in financial_issuers
        )
        # cache: (ts_code, endpoint) -> current-version ORIGINAL contract frame
        self._cache: dict[tuple[str, str], pd.DataFrame | None] = {}

    # ------------------------------------------------------------------ public

    @property
    def financial_issuers(self) -> frozenset[str]:
        return self._financial

    def as_of(
        self,
        trade_date: str | date,
        fields: Sequence[str],
        instruments: Sequence[str],
    ) -> pd.DataFrame:
        """PIT-correct financial values as-of ``trade_date``.

        ``instruments`` may be qlib-style (``SH600000``) or Tushare ts_code
        (``600000.SH``); both normalize to ts_code (the store key). Returns a
        DataFrame indexed by ts_code (financial issuers EXCLUDED), one column
        per requested charter field, each cell the disclosure-of-record value of
        that instrument's latest already-available report period, or NA.
        """
        _require_collection(fields, "fields")
        _require_collection(instruments, "instruments")
        td = self._to_date(trade_date)
        unknown = [f for f in fields if f not in _FIELD_ENDPOINT]
        if unknown:
            raise FinancialPITViewError(
                f"unknown charter field(s) {unknown}; valid: "
                f"{sorted(_FIELD_ENDPOINT)}"
            )
        by_endpoint: dict[str, list[str]] = {}
        for f in fields:
            by_endpoint.setdefault(_FIELD_ENDPOINT[f], []).append(f)

        rows: dict[str, dict[str, Any]] = {}
        for raw_ts in instruments:
            ts = self._to_ts_code(raw_ts)
            if ts in self._financial:
                continue  # financial-sector issuer excluded from the universe
            row: dict[str, Any] = {}
            for endpoint, endpoint_fields in by_endpoint.items():
                latest = self._latest_as_of(ts, endpoint, td, endpoint_fields)
                for f in endpoint_fields:
                    if latest is None:
                        row[f] = pd.NA
                    else:
                        v = latest.get(f)
                        row[f] = pd.NA if (v is None or pd.isna(v)) else v
            rows[ts] = row
        if not rows:
            # every requested instrument was financial-excluded (or the list was
            # empty): return an empty, correctly-columned frame so downstream
            # ``panel[field]`` never KeyErrors.
            out = pd.DataFrame(columns=list(fields))
        else:
            out = pd.DataFrame.from_dict(rows, orient="index", columns=list(fields))
        out.index.name = "instrument"
        return out

    def cross_check_exclusion(
        self, instruments: Sequence[str],
    ) -> list[ExclusionDisagreement]:
        """Report financial-exclusion cross-check disagreements (never resolve
        them silently, per spec): a financial-listed issuer that DOES report
        ``oper_cost``, or a non-excluded issuer that NEVER reports it."""
        _require_collection(instruments, "instruments")
        out: list[ExclusionDisagreement] = []
        for raw_ts in instruments:
            ts = self._to_ts_code(raw_ts)
            has_oper_cost = self._ever_reports(ts, "income", _CROSS_CHECK_FIELD)
            if ts in self._financial and has_oper_cost:
                out.append(ExclusionDisagreement(ts, "financial_has_oper_cost"))
            elif ts not in self._financial and has_oper_cost is False:
                out.append(
                    ExclusionDisagreement(ts, "nonfinancial_never_reports_oper_cost")
                )
        return out

    def coverage(
        self, field: str, instruments: Sequence[str], trade_date: str | date,
    ) -> float:
        """Fraction of the (non-financial) research universe with a non-NA
        as-of value for ``field`` on ``trade_date``."""
        panel = self.as_of(trade_date, [field], instruments)
        if panel.empty:
            return 0.0
        return float(panel[field].notna().mean())

    def assert_coverage_floor(
        self,
        floors: dict[str, float],
        instruments: Sequence[str],
        trade_date: str | date,
    ) -> None:
        """Fail loud if any field's as-of coverage falls below its recorded
        Gate-1 acceptance floor (spec: a coverage regression is never silently
        tolerated). ``floors`` maps field -> minimum acceptable fraction."""
        below: dict[str, tuple[float, float]] = {}
        for field, floor in floors.items():
            cov = self.coverage(field, instruments, trade_date)
            if cov < floor:
                below[field] = (round(cov, 4), floor)
        if below:
            raise FinancialPITViewError(
                "financial-PIT coverage below the Gate-1 acceptance floor "
                f"(field -> (actual, floor)): {below}. A field regressing below "
                "its recorded floor must be investigated, never tolerated."
            )

    # ---------------------------------------------------------------- internal

    def _latest_as_of(
        self, ts_code: str, endpoint: str, td: date, fields: Sequence[str],
    ) -> pd.Series | None:
        """The disclosure of record for the LATEST report_period already
        AVAILABLE as-of ``td``, or None if the instrument has no store file or
        nothing is available yet. Serves the latest already-announced period and
        holds it forward — but NEVER a stale carry-forward from an older period
        when a newer one is already announced (阶段8 Gate-2 correction).

        A store frame that EXISTS but lacks a requested charter column is a
        schema/store corruption (an old or bad ingest) — fail loud rather than
        serve it as ordinary per-row NA (codex #342 P2). A per-row NA (column
        present, value absent) is still legitimate missingness."""
        disclosure = self._disclosure_frame(ts_code, endpoint)
        if disclosure is None:
            return None
        # Column corruption is checked BEFORE the empty-frame return (codex #342
        # r6): a store with no disclosure-of-record rows (an empty / corrupt
        # parquet) that is ALSO missing a charter column must fail loud, not be
        # silently treated as "no data available yet".
        missing_cols = [f for f in fields if f not in disclosure.columns]
        if missing_cols:
            raise FinancialPITViewError(
                f"{ts_code}/{endpoint}: store frame is missing charter column(s) "
                f"{missing_cols} — a schema/store corruption, not per-row NA; "
                "refusing to serve. Re-ingest this endpoint (the PR-1 ingest "
                "keeps every charter column)."
            )
        if disclosure.empty:
            return None  # file exists with correct columns but no rows -> NA
        avail = disclosure[
            disclosure[AVAILABLE_FROM].map(
                lambda d: d is not None and d <= td,
            )
        ]
        if avail.empty:
            return None
        # the LATEST already-available report period (its disclosure of record),
        # held forward — not a fill, not a stale fall-back to an older period.
        return avail.sort_values(REPORT_PERIOD).iloc[-1]

    def _ever_reports(self, ts_code: str, endpoint: str, field: str) -> bool | None:
        """True/False whether the instrument ever discloses ``field`` (any
        disclosure-of-record row non-NA); None when the instrument has no store
        file.

        A store frame that EXISTS but lacks the ``field`` column is a schema/store
        corruption — fail loud rather than report ``False`` ("never reports"),
        which would silently mis-drive the exclusion cross-check (codex #342 r5)."""
        disclosure = self._disclosure_frame(ts_code, endpoint)
        if disclosure is None:
            return None
        if field not in disclosure.columns:
            raise FinancialPITViewError(
                f"{ts_code}/{endpoint}: store frame is missing charter column "
                f"{field!r} — a schema/store corruption; cannot run the exclusion "
                "cross-check. Re-ingest this endpoint (the PR-1 ingest keeps "
                "every charter column)."
            )
        return bool(disclosure[field].notna().any())

    def _disclosure_frame(self, ts_code: str, endpoint: str) -> pd.DataFrame | None:
        """Cached DISCLOSURE-OF-RECORD contract frame for an instrument/endpoint
        (per report_period: the ``update_flag=0`` row, or the sole
        ``update_flag=1`` row when the provider retained no original — 阶段8
        Gate-2 correction; the old ``update_flag=0`` FILTER dropped uf1-only
        recent periods and staled the served value 1–2 years). None when the
        instrument has no store file."""
        key = (ts_code, endpoint)
        if key in self._cache:
            return self._cache[key]
        path = self._store_dir / endpoint / f"{ts_code}.parquet"
        frame: pd.DataFrame | None
        if not path.is_file():
            frame = None
        else:
            raw = pd.read_parquet(path)
            contract = build_contract_frame(raw, self._calendar)
            current = resolve_current_versions(contract)
            frame = select_disclosure_of_record(current)
        self._cache[key] = frame
        return frame

    @staticmethod
    def _to_ts_code(instrument: str) -> str:
        """Normalize an inbound instrument to the store's ts_code key.

        The canonical universe is qlib-style (``SH600000``); the financial store
        is keyed by Tushare ``ts_code`` (``600000.SH.parquet``). Passing a
        qlib-style code straight through would probe a nonexistent file and
        silently return all-NA + skip ts_code-keyed exclusions — a no-silent-
        fallback violation (codex #342). Accept EITHER form, normalize to
        ts_code, and fail loud on anything that is neither (``qlib_to_ts_code``
        is lenient and would pass garbage through)."""
        ts = qlib_to_ts_code(str(instrument).strip())
        if not _TS_CODE_RE.match(ts):
            raise FinancialPITViewError(
                f"instrument {instrument!r} is neither a Tushare ts_code "
                "('600000.SH') nor a qlib-style code ('SH600000'); the financial "
                "store is ts_code-keyed and refuses to guess. Convert with "
                "src.data.pit._common.qlib_to_ts_code first."
            )
        return ts

    @staticmethod
    def _to_date(value: str | date) -> date:
        # datetime (incl. pandas Timestamp) subclasses date; normalize to a pure
        # date so the `date <= date` availability comparison never hits a
        # date-vs-datetime TypeError (codex #342 r8).
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        token = str(value).strip().replace("-", "")
        if len(token) == 8 and token.isdigit():
            return date(int(token[:4]), int(token[4:6]), int(token[6:8]))
        raise FinancialPITViewError(
            f"trade_date {value!r} must be a date or YYYY-MM-DD / YYYYMMDD."
        )
