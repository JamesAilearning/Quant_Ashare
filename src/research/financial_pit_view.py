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
* **Original-disclosure-first** — the ``update_flag=0`` value keyed to its
  announcement; undatable restatements are never back-applied (PR-1 contract).
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
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from src.data.pit.financial_pit_contract import (
    AVAILABLE_FROM,
    REPORT_PERIOD,
    build_contract_frame,
    resolve_current_versions,
)
from src.data.trading_calendar import StaticTradingCalendar
from src.data.tushare.financial_statements import DATA_FIELDS

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
    ``financial_issuers`` is the exclusion set (ts_codes); derive it with
    :func:`financial_issuers_from_industry` or pass an explicit list.
    """

    def __init__(
        self,
        store_dir: str | Path,
        calendar: StaticTradingCalendar,
        *,
        financial_issuers: Iterable[str] = (),
    ) -> None:
        self._store_dir = Path(store_dir)
        self._calendar = calendar
        self._financial = frozenset(str(t) for t in financial_issuers)
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

        Returns a DataFrame indexed by instrument (ts_code, financial issuers
        EXCLUDED), one column per requested charter field, each cell the value
        of that instrument's latest already-announced original statement, or NA.
        """
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
        for ts in instruments:
            ts = str(ts)
            if ts in self._financial:
                continue  # financial-sector issuer excluded from the universe
            row: dict[str, Any] = {}
            for endpoint, endpoint_fields in by_endpoint.items():
                latest = self._latest_as_of(ts, endpoint, td)
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
        out: list[ExclusionDisagreement] = []
        for ts in instruments:
            ts = str(ts)
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
        self, ts_code: str, endpoint: str, td: date,
    ) -> pd.Series | None:
        """The instrument's latest ALREADY-ANNOUNCED original statement as-of
        ``td`` (as-of carry-forward), or None if none is available yet."""
        original = self._original_frame(ts_code, endpoint)
        if original is None or original.empty:
            return None
        avail = original[
            original[AVAILABLE_FROM].map(
                lambda d: d is not None and d <= td,
            )
        ]
        if avail.empty:
            return None
        # carry-forward = the most recently announced period (NOT a fill).
        return avail.sort_values(REPORT_PERIOD).iloc[-1]

    def _ever_reports(self, ts_code: str, endpoint: str, field: str) -> bool | None:
        """True/False whether the instrument ever discloses ``field`` (any
        original row non-NA); None when the instrument has no store file."""
        original = self._original_frame(ts_code, endpoint)
        if original is None or original.empty or field not in original.columns:
            return None if original is None else False
        return bool(original[field].notna().any())

    def _original_frame(self, ts_code: str, endpoint: str) -> pd.DataFrame | None:
        """Cached current-version ``update_flag=0`` contract frame for an
        instrument/endpoint. None when the instrument has no store file."""
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
            frame = current[current["update_flag"].astype(str) == "0"].copy()
        self._cache[key] = frame
        return frame

    @staticmethod
    def _to_date(value: str | date) -> date:
        if isinstance(value, date):
            return value
        token = str(value).strip().replace("-", "")
        if len(token) == 8 and token.isdigit():
            return date(int(token[:4]), int(token[4:6]), int(token[6:8]))
        raise FinancialPITViewError(
            f"trade_date {value!r} must be a date or YYYY-MM-DD / YYYYMMDD."
        )
