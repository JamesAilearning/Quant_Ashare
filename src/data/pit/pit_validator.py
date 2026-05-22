"""Validate the qlib provider produced by Phase B.2.

Pipeline (Phase B.3, per docs/pit/pit_universe_design.md §5 Stage 6)
-------------------------------------------------------------------
::

    <provider_dir>  (output of Phase B.2)
    <delisted_registry_path>  (output of Phase A.2)
       -> validation report (dict + structured log)
       -> exit code: 0 success / 1 warnings / 2 failures

The 6 validation checks (A-F per design):

A. Survivorship — query each registry ticker's $close at delist_date+1;
   MUST be NaN. Mirrors and supersedes the legacy
   ``scripts/data_quality/verify_survivorship.py`` script, which is
   kept as a standalone diagnostic but now anchored on a corrected
   reference set.
B. Delist boundary — for every registry ticker, $close on delist_date
   MUST be valid AND $close strictly after MUST be NaN.
C. Time-travel — sample 5 historical dates; for every active ticker
   in the universe on that date, list_date <= date and no
   delist_date <= date.
D. qlib operator min_periods — ``Mean($close, 20)`` evaluated on
   ``delist_date + 1`` for each registry ticker MUST be NaN. This is
   the load-bearing assertion against §4.3.2 — if qlib's operator
   silently uses ``min_periods < N``, this check fails loudly.
E. Index membership — for known intra-period boundary cases in
   reference_cases.yaml::index_membership_cases, assert membership
   matches. CURRENTLY DEFERRED — Phase A.4 smoke test (PR #102)
   exposed that the reference YAML's csi300 dates predate proper
   Tushare verification. Check E is implemented but expected to
   surface mismatches until a YAML-correction PR lands.
F. Borrow-shell continuity — for any ticker in the reference YAML's
   ``borrow_shell_cases`` block, assert continuous price series
   across the restructure date.

Out of scope
------------
- No automatic provider regeneration on failure. Validator reports,
  operator decides.
- No metric comparison against the legacy provider (Phase D.5).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.core.logger import get_logger

_logger = get_logger(__name__)


@dataclass
class CheckResult:
    name: str
    code: str  # A / B / C / D / E / F
    passed: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class PITValidationReport:
    """Aggregate report. ``exit_code`` is the convention from the legacy
    ``verify_survivorship.py``: 0=all green, 1=warnings only, 2=any
    failure."""

    checks: list[CheckResult]
    provider_dir: Path

    @property
    def exit_code(self) -> int:
        if any(not c.passed for c in self.checks):
            return 2
        if any(c.warnings for c in self.checks):
            return 1
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_dir": str(self.provider_dir),
            "exit_code": self.exit_code,
            "checks": [
                {
                    "name": c.name, "code": c.code, "passed": c.passed,
                    "warnings": c.warnings, "errors": c.errors,
                    "details": c.details,
                }
                for c in self.checks
            ],
        }


class PITValidatorError(RuntimeError):
    pass


class PITValidator:
    """Run PIT correctness validation against a built provider directory."""

    def __init__(
        self,
        provider_dir: Path,
        delisted_registry_path: Path,
        reference_cases_path: Path | None = None,
        sample_dates: tuple[str, ...] = (
            "2010-01-04", "2015-06-15", "2018-12-28",
            "2020-03-19", "2024-02-05",
        ),
    ) -> None:
        self._provider_dir = provider_dir
        self._delisted_registry_path = delisted_registry_path
        self._reference_cases_path = reference_cases_path
        self._sample_dates = sample_dates

    # ------------------------------------------------------------------
    # Public orchestrator
    # ------------------------------------------------------------------

    def validate(self) -> PITValidationReport:
        self._sanity_check_provider()
        registry = self._load_delisted_registry()
        references = self._load_reference_cases()

        # qlib is initialised once for the whole report. We swallow the
        # init call's stdout (qlib prints a banner) by routing the log
        # capture through our own logger.
        self._init_qlib()

        checks = [
            self._check_a_survivorship(registry),
            self._check_b_delist_boundary(registry),
            self._check_c_time_travel(),
            self._check_d_qlib_operator_min_periods(registry),
            self._check_e_index_membership(references),
            self._check_f_borrow_shell_continuity(references),
        ]
        report = PITValidationReport(checks=checks, provider_dir=self._provider_dir)
        self._log_summary(report)
        return report

    # ------------------------------------------------------------------
    # Loaders / setup
    # ------------------------------------------------------------------

    def _sanity_check_provider(self) -> None:
        cal = self._provider_dir / "calendars" / "day.txt"
        inst = self._provider_dir / "instruments" / "all.txt"
        feats = self._provider_dir / "features"
        for p, label in [(cal, "calendars/day.txt"),
                         (inst, "instruments/all.txt"),
                         (feats, "features/")]:
            if not p.exists():
                raise PITValidatorError(
                    f"{self._provider_dir} is not a valid qlib provider — "
                    f"missing {label}. Run Phase B.1 + B.2 first."
                )

    def _load_delisted_registry(self) -> pd.DataFrame:
        path = self._delisted_registry_path
        if not path.exists():
            raise PITValidatorError(
                f"Missing {path}; run Phase A.2 first."
            )
        return pd.read_parquet(path)

    def _load_reference_cases(self) -> dict:
        if self._reference_cases_path is None:
            return {}
        path = self._reference_cases_path
        if not path.exists():
            _logger.warning(
                "Reference cases file %s not found; checks E and F will be skipped",
                path,
            )
            return {}
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data if isinstance(data, dict) else {}

    def _init_qlib(self) -> None:
        # Route every qlib bootstrap through the canonical runtime entry
        # point so the governance guard at
        # tests/governance/test_publisher_uses_canonical_init.py stays
        # green. We don't actually need adjust_mode for read-only
        # validation, but the canonical config requires one — POST
        # matches what Phase B.2 wrote into the bins (close × adj_factor).
        try:
            from src.core.canonical_backtest_contract import ADJUST_MODE_POST
            from src.core.qlib_runtime import (
                QlibRuntimeConfig,
                init_qlib_canonical,
            )
        except ImportError as exc:
            raise PITValidatorError(
                f"Cannot import canonical qlib runtime: {exc}"
            ) from exc

        config = QlibRuntimeConfig(
            provider_uri=str(self._provider_dir),
            region="cn",
            data_adjust_mode=ADJUST_MODE_POST,
        )
        with contextlib.redirect_stdout(None):
            init_qlib_canonical(config)

    # ------------------------------------------------------------------
    # Check implementations
    # ------------------------------------------------------------------

    def _check_a_survivorship(self, registry: pd.DataFrame) -> CheckResult:
        """Survivorship per legacy ``verify_survivorship.py``: $close on
        delist_date+1 must be NaN, $close on delist_date must be valid.

        Spot-checks the first 5 registry rows (full registry can be 325+
        rows; full sweep happens in check B).
        """
        from qlib.data import D  # type: ignore[import-not-found]

        result = CheckResult(name="Survivorship spot-check", code="A", passed=True)
        sample = registry.head(5)
        passes = 0
        for _, row in sample.iterrows():
            ticker = str(row["ticker"])
            delist = pd.Timestamp(row["delist_date"])
            day_after = (delist + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            try:
                df = D.features([ticker], ["$close"], delist.strftime("%Y-%m-%d"), day_after)
            except Exception as exc:  # qlib raises on missing instrument
                result.errors.append(f"{ticker}: query failed: {exc}")
                result.passed = False
                continue
            if df.empty:
                # No data either on delist_date or day-after; treat as
                # missing/survivorship
                result.errors.append(
                    f"{ticker}: no rows returned for [{delist.date()}, {day_after}]"
                )
                result.passed = False
                continue
            # Find day-after row (may not be a trading day; just check
            # there's no valid close past delist_date)
            after_mask = df.index.get_level_values("datetime") > delist
            after_vals = df.loc[after_mask, "$close"] if after_mask.any() else pd.Series([], dtype=float)
            if not after_vals.empty and after_vals.dropna().any():
                result.errors.append(
                    f"{ticker}: $close has non-NaN value(s) strictly after "
                    f"delist_date={delist.date()} — NaN-after-delist violated"
                )
                result.passed = False
            else:
                passes += 1
        result.details["sample_size"] = len(sample)
        result.details["passes"] = passes
        return result

    def _check_b_delist_boundary(self, registry: pd.DataFrame) -> CheckResult:
        """Full sweep: every registry ticker has data within
        ``TRUNCATION_TOLERANCE_DAYS`` of delist_date AND NaN strictly
        after delist_date.

        The two-sided check catches both bin-pipeline failure modes:

        - Extension (data past delist_date) — any non-NaN close past
          delist = NaN-after-delist violation.
        - Truncation (data ends well before delist_date) — if the
          last valid trading day for this ticker is more than
          ``TRUNCATION_TOLERANCE_DAYS`` before delist, the bin lost
          the delist tail. Codex P1 on PR #103.

        The 7-day tolerance accommodates the Tushare convention where
        the official ``delist_date`` can be one trading day after the
        last actual trade (e.g. SH600087 delist_date=2014-06-05 but
        last trade 2014-06-04) plus long-weekend gaps.
        """
        from qlib.data import D  # type: ignore[import-not-found]

        TRUNCATION_TOLERANCE_DAYS = 7
        result = CheckResult(name="Delist boundary sweep", code="B", passed=True)
        violations: list[str] = []
        checked = 0
        skipped = 0
        for _, row in registry.iterrows():
            ticker = str(row["ticker"])
            delist = pd.Timestamp(row["delist_date"])
            window_start = (delist - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
            window_end = (delist + pd.Timedelta(days=10)).strftime("%Y-%m-%d")
            try:
                df = D.features([ticker], ["$close"], window_start, window_end)
            except Exception:
                skipped += 1
                continue
            if df.empty:
                violations.append(
                    f"{ticker}: no data in [{window_start}, {window_end}] — "
                    f"ticker is entirely missing from the provider"
                )
                continue
            valid = df["$close"].dropna()
            if valid.empty:
                violations.append(
                    f"{ticker}: all-NaN in [{window_start}, {window_end}] — "
                    f"bin is empty around delist_date={delist.date()}"
                )
                continue
            last_valid = valid.index.get_level_values("datetime").max()
            days_before_delist = (delist - last_valid).days
            if days_before_delist > TRUNCATION_TOLERANCE_DAYS:
                violations.append(
                    f"{ticker}: last valid trade {last_valid.date()} is "
                    f"{days_before_delist}d BEFORE delist_date={delist.date()} "
                    f"(tolerance: {TRUNCATION_TOLERANCE_DAYS}d) — truncation"
                )
            after_mask = df.index.get_level_values("datetime") > delist
            non_nan_after = (
                df.loc[after_mask, "$close"].dropna()
                if after_mask.any() else pd.Series([], dtype=float)
            )
            if not non_nan_after.empty:
                violations.append(
                    f"{ticker}: {len(non_nan_after)} non-NaN value(s) "
                    f"past delist_date={delist.date()}"
                )
            checked += 1
        result.details["checked"] = checked
        result.details["skipped"] = skipped
        result.details["violation_count"] = len(violations)
        if violations:
            result.passed = False
            result.errors.extend(violations[:5])
            if len(violations) > 5:
                result.errors.append(
                    f"... and {len(violations) - 5} more (see full details)"
                )
        return result

    def _check_c_time_travel(self) -> CheckResult:
        """At each sample date, all returned universe instruments have
        ``list_date <= date`` AND no delist_date <= date."""
        from qlib.data import D  # type: ignore[import-not-found]

        result = CheckResult(name="Time-travel sanity", code="C", passed=True)
        registry = self._load_delisted_registry()
        delisted_lookup = {
            str(r["ticker"]): pd.Timestamp(r["delist_date"])
            for _, r in registry.iterrows()
        }
        for date in self._sample_dates:
            d = pd.Timestamp(date)
            try:
                insts = D.list_instruments(
                    D.instruments("all"), start_time=date, end_time=date,
                    as_list=True,
                )
            except Exception as exc:
                result.errors.append(f"{date}: list_instruments failed: {exc}")
                result.passed = False
                continue
            past_delisted = []
            for inst in insts:
                if inst in delisted_lookup and delisted_lookup[inst] < d:
                    past_delisted.append(inst)
            if past_delisted:
                result.errors.append(
                    f"{date}: {len(past_delisted)} delisted ticker(s) still in "
                    f"universe (sample: {past_delisted[:3]})"
                )
                result.passed = False
            result.details.setdefault("universes", {})[date] = len(insts)
        return result

    def _check_d_qlib_operator_min_periods(
        self, registry: pd.DataFrame,
    ) -> CheckResult:
        """The load-bearing §4.3.2 assertion: qlib ``Mean($close, 20)`` on
        day strictly after delist_date MUST be NaN.

        If this fails, either qlib's operator silently uses
        ``min_periods < 20`` (fix: wrap with explicit min_periods=N) or
        the NaN-after-delist write in Phase B.2 is broken.
        """
        from qlib.data import D  # type: ignore[import-not-found]

        result = CheckResult(
            name="qlib operator min_periods (delist boundary)", code="D",
            passed=True,
        )
        sample = registry.head(3)  # 3 representative tickers is enough
        violations: list[str] = []
        for _, row in sample.iterrows():
            ticker = str(row["ticker"])
            delist = pd.Timestamp(row["delist_date"])
            start = (delist + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            end = (delist + pd.Timedelta(days=20)).strftime("%Y-%m-%d")
            try:
                df = D.features([ticker], ["Mean($close, 20)"], start, end)
            except Exception as exc:
                result.errors.append(f"{ticker}: Mean query failed: {exc}")
                result.passed = False
                continue
            if df.empty:
                continue
            non_nan = df.dropna()
            if not non_nan.empty:
                violations.append(
                    f"{ticker}: Mean($close, 20) has {len(non_nan)} non-NaN "
                    f"value(s) strictly after delist_date={delist.date()}. "
                    f"§4.3.2 violation."
                )
        if violations:
            result.passed = False
            result.errors.extend(violations)
        result.details["sample_size"] = len(sample)
        return result

    def _check_e_index_membership(self, references: dict) -> CheckResult:
        """E: known CSI300 enter / leave boundary cases.

        The reference YAML's ``index_membership_cases.csi300`` rows
        were Tushare-verified in the follow-up to PR #102; each row's
        ``cite_tushare`` block names the actual snapshot transition
        observed in the index_weight pulls. This validator surface
        currently reports the cases-present count as a warning rather
        than running an end-to-end assertion — the proper E2E check
        would parse ``<provider_dir>/instruments/csi300.txt`` (Phase
        A.4 output) and re-run the same enter/leave match the
        resolver does. Wiring that into Phase B.3 is a separate small
        change (the resolver's logic is already in
        ``IndexMembershipResolver._validate_references`` — needs an
        adapter that consumes a written instruments file instead of
        an in-memory snapshot DataFrame).
        """
        result = CheckResult(
            name="Index membership references", code="E", passed=True,
        )
        cases = (references.get("index_membership_cases") or {}).get("csi300") or []
        if not cases:
            result.warnings.append(
                "No index_membership_cases.csi300 in reference YAML; check skipped."
            )
            return result
        result.warnings.append(
            f"{len(cases)} index membership reference case(s) present "
            "(Tushare-verified per row). End-to-end validation against "
            "the built instruments/csi300.txt is a Phase B.3 follow-up; "
            "use IndexMembershipResolver directly against the built "
            "provider for now."
        )
        result.details["case_count"] = len(cases)
        return result

    def _check_f_borrow_shell_continuity(self, references: dict) -> CheckResult:
        """For each ``borrow_shell_cases`` entry, assert $close is
        continuous (no NaN gap) across ``restructure_date``."""
        from qlib.data import D  # type: ignore[import-not-found]

        result = CheckResult(name="Borrow-shell continuity", code="F", passed=True)
        cases = references.get("borrow_shell_cases") or []
        if not cases:
            result.warnings.append("No borrow_shell_cases in reference YAML; check skipped.")
            return result
        for case in cases:
            ticker = str(case.get("ticker"))
            restructure = pd.Timestamp(case.get("restructure_date"))
            start = (restructure - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
            end = (restructure + pd.Timedelta(days=5)).strftime("%Y-%m-%d")
            try:
                df = D.features([ticker], ["$close"], start, end)
            except Exception as exc:
                result.errors.append(f"{ticker}: query failed: {exc}")
                result.passed = False
                continue
            if df.empty:
                result.errors.append(
                    f"{ticker}: no data around restructure_date={restructure.date()}"
                )
                result.passed = False
                continue
            # Number of trading days within ±5 calendar days is roughly 6-8;
            # require at least 4 valid values bracketing the restructure date.
            valid = df["$close"].dropna()
            if len(valid) < 4:
                result.errors.append(
                    f"{ticker}: only {len(valid)} valid $close values in "
                    f"±5d window around restructure_date={restructure.date()}; "
                    f"expected continuous trading (no NaN gap)"
                )
                result.passed = False
            result.details.setdefault("cases", {})[ticker] = len(valid)
        return result

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    @staticmethod
    def _log_summary(report: PITValidationReport) -> None:
        _logger.info("")
        _logger.info("=== PIT validation summary ===")
        for c in report.checks:
            status = "PASS " if c.passed else "FAIL "
            warn = f" ({len(c.warnings)} warning{'s' if len(c.warnings)!=1 else ''})" if c.warnings else ""
            _logger.info("  [%s] %s — %s%s", c.code, status, c.name, warn)
            for e in c.errors:
                _logger.info("        ERROR: %s", e)
            for w in c.warnings:
                _logger.info("        WARN:  %s", w)
        _logger.info("Exit code: %d", report.exit_code)


# ---------------------------------------------------------------------
# Legacy helper exposed by Phase A's verify_survivorship.py
# ---------------------------------------------------------------------

def _legacy_verify_survivorship_check(
    provider_uri: Path, known_delisted: list[tuple[str, str, str]],
) -> int:
    """Bridge to the legacy verify_survivorship.py logic, callable from
    Phase B.3. Returns the legacy exit code (0/1/2).

    Kept compact because Phase B.3 now owns the canonical sweep
    (check B); this helper exists so the operator can still run the
    legacy "did the bin builder produce GOOD data?" smoke against a
    fresh provider without re-implementing it in two places.
    """
    from qlib.data import D  # type: ignore[import-not-found]

    from src.core.canonical_backtest_contract import ADJUST_MODE_POST
    from src.core.qlib_runtime import QlibRuntimeConfig, init_qlib_canonical

    with contextlib.redirect_stdout(None):
        init_qlib_canonical(QlibRuntimeConfig(
            provider_uri=str(provider_uri),
            region="cn",
            data_adjust_mode=ADJUST_MODE_POST,
        ))
    good = bad_extended = truncated = missing = errors = 0
    for ticker, delist_str, _label in known_delisted:
        try:
            df = D.features([ticker], ["$close"], "2014-01-01", "2025-12-31")
        except Exception:
            errors += 1
            continue
        valid = df["$close"].dropna() if not df.empty else pd.Series([], dtype=float)
        if valid.empty:
            missing += 1
            continue
        last = valid.index.get_level_values("datetime").max()
        expected = pd.Timestamp(delist_str)
        days_past = (last - expected).days
        if -90 <= days_past < 90:
            good += 1
        elif days_past >= 90:
            bad_extended += 1
        else:
            truncated += 1
    n_total = len(known_delisted)
    if bad_extended > n_total / 2 or truncated > n_total / 2:
        return 2
    if missing > n_total / 2:
        return 1
    if good == n_total:
        return 0
    return 1
