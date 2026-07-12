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
    REPORT_PERIOD,
    FinancialPITContractError,
    select_disclosure_of_record,
    version_collapse_residual,
)

_P1, _P2, _P3 = date(2021, 12, 31), date(2022, 3, 31), date(2022, 6, 30)


def _frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


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


def test_audit_empty_frame_is_zero_residual():
    res = version_collapse_residual(_frame([]), ["revenue"])
    assert res.n_both_version_periods == 0
    assert res.overall_differing_fraction() == 0.0
