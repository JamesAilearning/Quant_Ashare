"""Version-collapse honesty-envelope audit (阶段8 Gate-2 correction).

Audits the restatement residual — across report periods that have BOTH an
``update_flag=0`` and an ``update_flag=1`` row, the fraction whose values DIFFER
(a genuine restatement) vs are EQUAL (a version marker only) — and asserts the
serve-rule (:func:`select_disclosure_of_record`, prefer uf0) resolves EVERY
differing period to ``update_flag=0``, so a non-zero residual introduces NO
look-ahead (spec ``v2-financial-pit-contract`` version-collapse audit).

CI runs the audit MECHANISM on a synthetic fixture (deterministic, no store).
The full-CSI300-ever residual number is produced by running the SAME
:func:`version_collapse_residual` over the ingested store (see the PR's smoke
re-run) and recorded as the documented restatement residual.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.data.pit.financial_pit_contract import (
    ANNOUNCEMENT_DATE,
    AVAILABLE_FROM,
    REPORT_PERIOD,
    FinancialPITContractError,
    select_disclosure_of_record,
    version_collapse_residual,
)
from src.data.tushare.financial_statements import COL_CONTENT_HASH

_P1, _P2, _P3 = date(2021, 12, 31), date(2022, 3, 31), date(2022, 6, 30)

# announcement identity defaults (spec fix-financial-ingest-ambiguous-duplicates:
# the announcement dates + availability are part of the record-selection
# contract; records order by announcement day, availability is the tiebreak).
_DEFAULT_FANN = "20220430"
_DEFAULT_ANN = date(2022, 4, 30)
_DEFAULT_AVAIL = date(2022, 5, 5)
_LATE_FANN = "20230430"
_LATE_ANN = date(2023, 4, 30)
_LATE_AVAIL = date(2023, 5, 4)


def _frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    filled = []
    for i, r in enumerate(rows):
        r = dict(r)
        r.setdefault("f_ann_date", _DEFAULT_FANN)
        r.setdefault("ann_date", _DEFAULT_FANN)
        r.setdefault(ANNOUNCEMENT_DATE, _DEFAULT_ANN)
        r.setdefault(AVAILABLE_FROM, _DEFAULT_AVAIL)
        r.setdefault(COL_CONTENT_HASH, f"h{i}")  # unique per row by default
        filled.append(r)
    return pd.DataFrame(filled)


def _current_versions() -> pd.DataFrame:
    # P1: both versions EQUAL (a marker); P2: both versions DIFFER (a genuine
    # restatement); P3: update_flag=1 ONLY (provider kept no original).
    return _frame([
        {"ts_code": "X", REPORT_PERIOD: _P1, "update_flag": "0", "revenue": 100.0},
        {"ts_code": "X", REPORT_PERIOD: _P1, "update_flag": "1", "revenue": 100.0},
        {"ts_code": "X", REPORT_PERIOD: _P2, "update_flag": "0", "revenue": 50.0},
        {"ts_code": "X", REPORT_PERIOD: _P2, "update_flag": "1", "revenue": 55.0},
        {"ts_code": "X", REPORT_PERIOD: _P3, "update_flag": "1", "revenue": 60.0},
    ])


def test_audit_records_differing_version_fraction():
    res = version_collapse_residual(_current_versions(), ["revenue"])
    # only P1 and P2 have BOTH versions; P3 is uf1-only (not a comparison).
    assert res.n_both_version_periods == 2
    assert res.per_field["revenue"] == (2, 1)        # 2 compared, 1 differs
    assert res.overall_differing_fraction() == 0.5
    assert res.differing == [("X", _P2, "revenue")]  # the genuine restatement


def test_audit_counts_na_transition_as_difference():
    # a both-version period where a field goes NA -> populated (or vice versa) is
    # a restatement and MUST be counted as differing, not skipped (codex #345 r4).
    frame = _frame([
        {"ts_code": "Y", REPORT_PERIOD: _P1, "update_flag": "0", "revenue": pd.NA},
        {"ts_code": "Y", REPORT_PERIOD: _P1, "update_flag": "1", "revenue": 100.0},
    ])
    res = version_collapse_residual(frame, ["revenue"])
    assert res.per_field["revenue"] == (1, 1)          # compared 1, differs 1
    assert res.differing == [("Y", _P1, "revenue")]


def test_audit_both_na_is_not_a_comparison():
    # neither version discloses the field -> not a comparison, not a difference.
    frame = _frame([
        {"ts_code": "Z", REPORT_PERIOD: _P1, "update_flag": "0", "revenue": pd.NA},
        {"ts_code": "Z", REPORT_PERIOD: _P1, "update_flag": "1", "revenue": pd.NA},
    ])
    res = version_collapse_residual(frame, ["revenue"])
    assert res.per_field["revenue"] == (0, 0)


def test_serve_rule_resolves_differing_period_to_uf0_no_lookahead():
    picked = select_disclosure_of_record(_current_versions())
    served = {row[REPORT_PERIOD]: row["revenue"] for _, row in picked.iterrows()}
    assert served[_P1] == 100.0                       # equal both-version -> uf0
    assert served[_P2] == 50.0                        # differing -> uf0, never 55
    assert served[_P3] == 60.0                        # uf1-only kept (served)
    # exactly one row per period (collapsed)
    assert len(picked) == 3


def test_audit_fails_loud_on_absent_field():
    with pytest.raises(FinancialPITContractError, match="missing field column"):
        version_collapse_residual(_current_versions(), ["not_a_field"])


def test_audit_schema_valid_empty_frame_is_zero_residual():
    # a SCHEMA-VALID empty frame (columns present, zero rows) is a legit zero
    # residual; but a schemaless pd.DataFrame() (a miswired audit) must fail loud
    # rather than silently report 0% (codex #345 r3).
    empty = pd.DataFrame(columns=[
        "ts_code", REPORT_PERIOD, "update_flag", "f_ann_date", "ann_date",
        ANNOUNCEMENT_DATE, AVAILABLE_FROM, COL_CONTENT_HASH, "revenue",
    ])
    res = version_collapse_residual(empty, ["revenue"])
    assert res.n_both_version_periods == 0
    assert res.overall_differing_fraction() == 0.0
    with pytest.raises(FinancialPITContractError, match="missing field column"):
        version_collapse_residual(pd.DataFrame(), ["revenue"])


def test_fails_loud_on_unresolved_duplicate_batches():
    # an append-only frame with TWO physical rows for the SAME logical version
    # (ts_code, report_period, update_flag) — a changed re-fetch — must go through
    # resolve_current_versions first; the collapse / audit refuse rather than
    # treat a superseded batch as current (codex #345 P2).
    dup = _frame([
        {"ts_code": "X", REPORT_PERIOD: _P1, "update_flag": "0", "revenue": 100.0},
        {"ts_code": "X", REPORT_PERIOD: _P1, "update_flag": "0", "revenue": 111.0},
    ])
    with pytest.raises(FinancialPITContractError, match="duplicate logical versions"):
        version_collapse_residual(dup, ["revenue"])
    with pytest.raises(FinancialPITContractError, match="duplicate logical versions"):
        select_disclosure_of_record(dup)


# disambiguation by announcement date (fix-financial-ingest-ambiguous-duplicates)

def test_late_reannouncement_of_same_version_not_served():
    # one (period, uf1) with TWO dated disclosures: the earliest-announced row is
    # the record; the late re-announcement (different value) is a dated
    # restatement — recorded, never served.
    frame = _frame([
        {"ts_code": "W", REPORT_PERIOD: _P3, "update_flag": "1", "revenue": 60.0},
        {"ts_code": "W", REPORT_PERIOD: _P3, "update_flag": "1", "revenue": 99.0,
         "f_ann_date": _LATE_FANN, ANNOUNCEMENT_DATE: _LATE_ANN,
         AVAILABLE_FROM: _LATE_AVAIL},
    ])
    picked = select_disclosure_of_record(frame)
    assert len(picked) == 1
    assert picked.iloc[0]["revenue"] == 60.0           # earliest-announced wins
    assert str(picked.iloc[0]["f_ann_date"]) == _DEFAULT_FANN


def test_uf0_still_beats_late_and_early_uf1_disclosures():
    # both-version period where uf1 ALSO has a late re-announcement: uf0 record
    # is served (original-first unchanged by the key extension).
    frame = _frame([
        {"ts_code": "W", REPORT_PERIOD: _P1, "update_flag": "0", "revenue": 100.0},
        {"ts_code": "W", REPORT_PERIOD: _P1, "update_flag": "1", "revenue": 100.0},
        {"ts_code": "W", REPORT_PERIOD: _P1, "update_flag": "1", "revenue": 999.0,
         "f_ann_date": _LATE_FANN, ANNOUNCEMENT_DATE: _LATE_ANN,
         AVAILABLE_FROM: _LATE_AVAIL},
    ])
    picked = select_disclosure_of_record(frame)
    assert len(picked) == 1
    assert str(picked.iloc[0]["update_flag"]) == "0"
    assert picked.iloc[0]["revenue"] == 100.0


def test_record_ordered_by_announcement_day_not_availability():
    # two same-version disclosures on DIFFERENT announcement days that map to
    # ONE available_from (Friday post-close + weekend re-announcement -> same
    # Monday): the EARLIER-ANNOUNCED row must win regardless of provider row
    # order — availability alone cannot order them (codex #351).
    fri_ann, sat_ann = date(2022, 4, 29), date(2022, 4, 30)
    monday = date(2022, 5, 5)
    frame = _frame([
        # provider order puts the LATER announcement FIRST on purpose
        {"ts_code": "V", REPORT_PERIOD: _P2, "update_flag": "1", "revenue": 99.0,
         "f_ann_date": "20220430", ANNOUNCEMENT_DATE: sat_ann,
         AVAILABLE_FROM: monday},
        {"ts_code": "V", REPORT_PERIOD: _P2, "update_flag": "1", "revenue": 60.0,
         "f_ann_date": "20220429", ANNOUNCEMENT_DATE: fri_ann,
         AVAILABLE_FROM: monday},
    ])
    picked = select_disclosure_of_record(frame)
    assert len(picked) == 1
    assert picked.iloc[0]["revenue"] == 60.0           # earlier ANNOUNCED wins


def test_same_effective_day_pair_is_duplicate_identity():
    # two disclosures on ONE effective announcement day are ONE versioned
    # identity (effective-announcement key, codex #351 r5): an unresolved
    # same-day double content fails loud as duplicate versions — never
    # ordered by row order (an ann_date-only difference under a present
    # f_ann_date does NOT mint a second identity).
    frame = _frame([
        {"ts_code": "V", REPORT_PERIOD: _P2, "update_flag": "1", "revenue": 60.0},
        {"ts_code": "V", REPORT_PERIOD: _P2, "update_flag": "1", "revenue": 99.0,
         "ann_date": "20220501"},  # ann-only difference, SAME effective day
    ])
    with pytest.raises(FinancialPITContractError, match="duplicate logical versions"):
        select_disclosure_of_record(frame)
    with pytest.raises(FinancialPITContractError, match="duplicate logical versions"):
        version_collapse_residual(frame, ["revenue"])


def test_undated_disclosure_loses_to_dated_same_version():
    # a version with an UNDATED row (no availability) and a dated row: the dated
    # row is the record (the undated one can never serve anyway).
    frame = _frame([
        {"ts_code": "W", REPORT_PERIOD: _P2, "update_flag": "1", "revenue": 70.0,
         "f_ann_date": None, "ann_date": None, ANNOUNCEMENT_DATE: None,
         AVAILABLE_FROM: None},
        {"ts_code": "W", REPORT_PERIOD: _P2, "update_flag": "1", "revenue": 71.0},
    ])
    picked = select_disclosure_of_record(frame)
    assert len(picked) == 1
    assert picked.iloc[0]["revenue"] == 71.0


def test_audit_compares_records_not_late_reannouncements():
    # residual compares uf0-record vs uf1-record; the late uf1 re-announcement
    # (999) must not swap into the comparison.
    frame = _frame([
        {"ts_code": "W", REPORT_PERIOD: _P1, "update_flag": "0", "revenue": 100.0},
        {"ts_code": "W", REPORT_PERIOD: _P1, "update_flag": "1", "revenue": 100.0},
        {"ts_code": "W", REPORT_PERIOD: _P1, "update_flag": "1", "revenue": 999.0,
         "f_ann_date": _LATE_FANN, ANNOUNCEMENT_DATE: _LATE_ANN,
         AVAILABLE_FROM: _LATE_AVAIL},
    ])
    res = version_collapse_residual(frame, ["revenue"])
    assert res.n_both_version_periods == 1
    assert res.per_field["revenue"] == (1, 0)          # records equal -> no differ


def test_fails_loud_on_non_binary_update_flag():
    # a legacy/corrupt row with update_flag not in {0,1} must NOT be silently
    # ranked as a revision and served as disclosure of record — fail loud
    # (codex #345 r2).
    bad = _frame([
        {"ts_code": "X", REPORT_PERIOD: _P1, "update_flag": "2", "revenue": 100.0},
    ])
    with pytest.raises(FinancialPITContractError, match="non-0/1 value"):
        select_disclosure_of_record(bad)
    with pytest.raises(FinancialPITContractError, match="non-0/1 value"):
        version_collapse_residual(bad, ["revenue"])
