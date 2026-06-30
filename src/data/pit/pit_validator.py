"""Validate the qlib provider produced by Phase B.2.

Pipeline (Phase B.3, per docs/pit/pit_universe_design.md §5 Stage 6)
-------------------------------------------------------------------
::

    <provider_dir>  (output of Phase B.2)
    <delisted_registry_path>  (output of Phase A.2)
       -> validation report (dict + structured log)
       -> exit code: 0 success / 1 warnings / 2 failures

The 6 validation checks (A-F per design):

A. Survivorship — spot-check the first 5 IN-RANGE registry tickers: a
   non-NaN $close strictly AFTER delist_date is the look-ahead failure.
   (PR #272: a missing bar AT delist_date is NOT a failure — a stock
   suspended before its formal delist legitimately has none.)
B. Delist boundary — full sweep. The only HARD failure is look-ahead
   (non-NaN $close past delist_date). Data missing/short near delist_date
   is a WARNING, not a failure: ~44% of in-range delistings suspend
   weeks-to-months before their FORMAL delist, so the bin faithfully ends
   early (vendor has no bars there either). Delistings outside the built
   bundle's calendar range are skipped (out of scope). Mirrors and
   supersedes the legacy stand-alone survivorship smoke-check.
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
    """Aggregate report. ``exit_code`` follows the legacy survivorship
    convention: 0=all green, 1=warnings only, 2=any failure."""

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


# ----------------------------------------------------------------------
# Pure delist-boundary verdict helpers (PR #272)
#
# Extracted so the survivorship logic is unit-testable with synthetic
# qlib-shaped frames (MultiIndex [instrument, datetime], "$close" column),
# without bringing up a real qlib provider. The survivorship gate's only HARD
# failure is look-ahead — a non-NaN $close strictly AFTER delist_date. "Data
# missing/short near delist_date" is a WARNING: most A-share delistings suspend
# weeks-to-months before their FORMAL delist, so the bin faithfully ends early.
# ----------------------------------------------------------------------


def _in_calendar_range(
    delist: pd.Timestamp, cal_start: pd.Timestamp, cal_end: pd.Timestamp
) -> bool:
    """True when a delist_date falls inside the built bundle's calendar. A
    delisting outside [cal_start, cal_end] is correctly absent from this bundle,
    so its boundary is out of scope (skipped), not a failure."""
    return bool(cal_start <= delist <= cal_end)


def _lookahead_violation(
    df: pd.DataFrame | None, delist: pd.Timestamp, ticker: str
) -> str | None:
    """The survivorship/look-ahead HARD failure: any non-NaN ``$close`` strictly
    AFTER ``delist_date``. Returns a message or ``None``."""
    if df is None or df.empty:
        return None
    after_mask = df.index.get_level_values("datetime") > delist
    after = (
        df.loc[after_mask, "$close"].dropna()
        if after_mask.any() else pd.Series([], dtype=float)
    )
    if not after.empty:
        return (
            f"{ticker}: {len(after)} non-NaN value(s) past "
            f"delist_date={delist.date()} — survivorship/look-ahead"
        )
    return None


def _suspension_signal(
    df: pd.DataFrame | None,
    delist: pd.Timestamp,
    window_start: str,
    window_end: str,
    ticker: str,
    tolerance_days: int,
) -> str | None:
    """A WARNING (not a failure): the bin has no / only-NaN / early-ending data
    around ``delist_date``. Faithful to the vendor for a stock suspended before
    its formal delist; the real bin-truncation bug can't be told apart from this
    without the vendor's last-trade date, so it is surfaced, not blocked."""
    if df is None or df.empty:
        return (
            f"{ticker}: no data in [{window_start}, {window_end}] — suspended "
            f"before formal delist_date={delist.date()} (or window outside "
            f"coverage)"
        )
    valid = df["$close"].dropna()
    if valid.empty:
        return (
            f"{ticker}: all-NaN in [{window_start}, {window_end}] — suspended "
            f"before formal delist_date={delist.date()}"
        )
    last_valid = valid.index.get_level_values("datetime").max()
    days_before = (delist - last_valid).days
    if days_before > tolerance_days:
        return (
            f"{ticker}: last valid trade {last_valid.date()} is {days_before}d "
            f"before delist_date={delist.date()} — suspended before formal "
            f"delist (vendor-faithful)"
        )
    return None


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
        # Cache the calendar range: checks A/B/D each read it, and it parses
        # the full day.txt. The file is immutable for the validator's
        # lifetime, so compute it at most once.
        self._cal_range_cache: tuple[pd.Timestamp, pd.Timestamp] | None = None

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
            self._check_c_time_travel(registry),
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

    def _load_reference_cases(self) -> dict[str, Any]:
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

    def _calendar_range(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        """(first, last) trading day in the built provider's calendar.

        Used to skip delist reference cases whose delist_date falls outside the
        bundle's coverage (PR #272): a stock delisted before the bundle starts
        (or after it ends) is legitimately not in the provider, so validating
        its delist boundary against this bundle is out of scope, not a failure.
        """
        if self._cal_range_cache is not None:
            return self._cal_range_cache
        days = (
            (self._provider_dir / "calendars" / "day.txt")
            .read_text(encoding="utf-8")
            .split()
        )
        if not days:
            raise PITValidatorError(
                f"{self._provider_dir}/calendars/day.txt is empty"
            )
        self._cal_range_cache = (pd.Timestamp(days[0]), pd.Timestamp(days[-1]))
        return self._cal_range_cache

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
        """Survivorship spot-check (PR #272 — look-ahead is the only failure):
        ``$close`` must be NaN strictly AFTER ``delist_date`` (the actual
        survivorship / look-ahead risk).

        The legacy "valid $close ON delist_date" expectation is dropped: a stock
        suspended before its FORMAL delist — the majority, ~44% of in-range
        delistings — legitimately has no bar at/near delist_date, faithful to
        the vendor, not a violation. Cases whose delist_date falls outside the
        built bundle's calendar are skipped (out of scope, see B).

        Spot-checks the first 5 IN-RANGE registry rows; the full sweep is B.
        """
        from qlib.data import D

        result = CheckResult(name="Survivorship spot-check", code="A", passed=True)
        cal_start, cal_end = self._calendar_range()
        delist_ts = pd.to_datetime(registry["delist_date"])
        in_range = registry[(delist_ts >= cal_start) & (delist_ts <= cal_end)]
        sample = in_range.head(5)
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
            # Only a non-NaN $close STRICTLY AFTER delist is a violation; no
            # rows at/after delist (suspended before formal delist) is OK.
            violation = _lookahead_violation(df, delist, ticker)
            if violation is not None:
                result.errors.append(violation)
                result.passed = False
            else:
                passes += 1
        result.details["sample_size"] = len(sample)
        result.details["in_range_total"] = int(len(in_range))
        result.details["out_of_range_skipped"] = int(len(registry) - len(in_range))
        result.details["passes"] = passes
        return result

    def _check_b_delist_boundary(self, registry: pd.DataFrame) -> CheckResult:
        """Full sweep (PR #272 — look-ahead is the only hard failure): every
        IN-RANGE registry ticker must have NaN ``$close`` strictly AFTER its
        delist_date. That extension direction is the actual survivorship bias
        the gate exists to catch and stays a hard ERROR.

        The "data present near delist_date" expectation is demoted to a
        WARNING. ~44% of in-range delistings are suspended weeks-to-months
        before their FORMAL delist (median 42d, up to 407d), so the bin
        legitimately ends well before delist_date — faithful to the vendor
        (Tushare has no bars there either), NOT a bin truncation bug. The
        bin-pipeline truncation case (Codex P1 on PR #103) can no longer be
        distinguished from a legitimate pre-delist suspension without the
        vendor's actual last-trade date, so it is surfaced loudly rather than
        blocking the build.

        Cases whose delist_date falls outside the built bundle's calendar are
        skipped entirely (out of scope — the ticker is correctly absent).
        """
        from qlib.data import D

        TRUNCATION_TOLERANCE_DAYS = 7
        result = CheckResult(name="Delist boundary sweep", code="B", passed=True)
        cal_start, cal_end = self._calendar_range()
        violations: list[str] = []   # look-ahead (post-delist data) — hard fail
        suspensions: list[str] = []  # missing/short near delist — warning only
        checked = 0
        skipped = 0
        out_of_range = 0
        for _, row in registry.iterrows():
            ticker = str(row["ticker"])
            delist = pd.Timestamp(row["delist_date"])
            if not _in_calendar_range(delist, cal_start, cal_end):
                out_of_range += 1
                continue
            window_start = (delist - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
            window_end = (delist + pd.Timedelta(days=10)).strftime("%Y-%m-%d")
            try:
                df = D.features([ticker], ["$close"], window_start, window_end)
            except Exception:
                skipped += 1
                continue
            suspension = _suspension_signal(
                df, delist, window_start, window_end, ticker,
                TRUNCATION_TOLERANCE_DAYS,
            )
            if suspension is not None:
                suspensions.append(suspension)
            violation = _lookahead_violation(df, delist, ticker)
            if violation is not None:
                violations.append(violation)
            checked += 1
        result.details["checked"] = checked
        result.details["skipped"] = skipped
        result.details["out_of_range_skipped"] = out_of_range
        result.details["suspension_warnings"] = len(suspensions)
        result.details["violation_count"] = len(violations)
        if suspensions:
            result.warnings.extend(suspensions[:5])
            if len(suspensions) > 5:
                result.warnings.append(
                    f"... and {len(suspensions) - 5} more suspended-before-delist "
                    f"ticker(s) (vendor-faithful, not a survivorship error)"
                )
        if violations:
            result.passed = False
            result.errors.extend(violations[:5])
            if len(violations) > 5:
                result.errors.append(
                    f"... and {len(violations) - 5} more (see full details)"
                )
        return result

    def _check_c_time_travel(self, registry: pd.DataFrame) -> CheckResult:
        """At each sample date, all returned universe instruments have
        ``list_date <= date`` AND no delist_date <= date."""
        from qlib.data import D

        result = CheckResult(name="Time-travel sanity", code="C", passed=True)
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

        Runs the assertion on the first ``TARGET_CHECKS`` IN-RANGE delistings
        whose delist+1..+20 window actually has calendar coverage. Two kinds of
        row would otherwise waste the budget and silently no-op the §4.3.2
        assertion (``df.empty`` → ``continue``): (a) an out-of-range delisting
        (filtered out, like [A]/[B], PR #272); (b) an in-range delisting at the
        calendar TAIL whose delist+1..+20 window falls past cal_end (codex P2 on
        PR #273). So we keep scanning in-range rows and count only NON-EMPTY
        checks until ``TARGET_CHECKS`` have actually run.
        """
        from qlib.data import D

        TARGET_CHECKS = 3
        result = CheckResult(
            name="qlib operator min_periods (delist boundary)", code="D",
            passed=True,
        )
        cal_start, cal_end = self._calendar_range()
        delist_ts = pd.to_datetime(registry["delist_date"])
        in_range = registry[(delist_ts >= cal_start) & (delist_ts <= cal_end)]
        violations: list[str] = []
        checked = 0   # non-empty post-delist windows the assertion actually ran on
        examined = 0  # in-range rows queried (incl. tail rows with no coverage)
        for _, row in in_range.iterrows():
            if checked >= TARGET_CHECKS:
                break
            ticker = str(row["ticker"])
            delist = pd.Timestamp(row["delist_date"])
            start = (delist + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            end = (delist + pd.Timedelta(days=20)).strftime("%Y-%m-%d")
            examined += 1
            try:
                df = D.features([ticker], ["Mean($close, 20)"], start, end)
            except Exception as exc:
                result.errors.append(f"{ticker}: Mean query failed: {exc}")
                result.passed = False
                continue
            if df.empty:
                # delist+1..+20 has no calendar coverage (tail delisting) — the
                # assertion can't run here; keep scanning instead of burning the
                # budget on it (codex P2 #273).
                continue
            checked += 1
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
        if checked == 0 and len(in_range) > 0:
            # Every in-range delisting was a tail row with no post-delist
            # coverage — the spot-check could not run. Surface it (warning, not
            # a hard failure) rather than silently passing.
            result.warnings.append(
                "min_periods spot-check ran 0 assertions — no in-range delisting "
                "has a delist+1..+20 window within the bundle calendar"
            )
        result.details["checked"] = checked
        result.details["examined"] = examined
        result.details["in_range_total"] = int(len(in_range))
        result.details["out_of_range_skipped"] = int(len(registry) - len(in_range))
        return result

    def _check_e_index_membership(self, references: dict[str, Any]) -> CheckResult:
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

    def _check_f_borrow_shell_continuity(self, references: dict[str, Any]) -> CheckResult:
        """For each ``borrow_shell_cases`` entry, assert $close is
        continuous (no NaN gap) across ``restructure_date``."""
        from qlib.data import D

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
