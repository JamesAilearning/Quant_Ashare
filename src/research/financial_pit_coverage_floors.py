"""Canonical financial-PIT coverage floors (阶段8 Gate-3 Step-A).

THE canonical field->floor mapping the spec's coverage-acceptance requirement
refers to — closing the codex #343 gap ("no canonical list of required
fields/floors; a real coverage regression could pass by omitting or
mis-supplying the intended floors"). Consumers MUST pass ``COVERAGE_FLOORS``
to :meth:`FinancialPITDataView.assert_coverage_floor` rather than inventing
ad-hoc floors.

Floors are AS-OF measured values (what the view actually serves under the
disclosure-of-record rule), set from the Gate-3 Step-A canonical report
(``docs/research/gate3_step_a_pit_coverage_report.md``) over ex-financial
CSI300 members at quarterly as-of snapshots:

    floor(field) = round(min over 2019-2025 of yearly mean coverage, 2) - 0.02

(2018 is excluded from the minimum: rd_exp's pre-standard sparsity is a known
regime, recorded in the report, and the C2 window starts 2019+.) The -0.02
margin absorbs snapshot jitter without tolerating a real regression. A field
regressing below its floor fails loud (assert_coverage_floor) — investigated,
never tolerated.

**Floors are PER-UNIVERSE.** Coverage is a property of the universe's issuer
mix, not of the store alone, so a floor calibrated on one universe is not a
valid tripwire for another: enforcing the CSI300 set against CSI800 reports
breaches that are universe differences, not regressions (and vice versa would
silently loosen the CSI300 guard). Each universe therefore gets its own
measured set — :data:`COVERAGE_FLOORS` (CSI300) and
:data:`CSI800_COVERAGE_FLOORS` — derived by the SAME rule above, and the
caller passes the set matching the universe it is measuring.
"""
from __future__ import annotations

from typing import Final

# field -> minimum acceptable as-of coverage fraction (ex-financial members).
# Values = round(min over 2019-2025 of yearly mean as-of coverage, 2) - 0.02,
# from the Step-A report tables. adv_receipts / contract_liab floors are LOW by
# regime (the 2020 预收→合同负债 reclassification splits disclosure between
# them); the candidate-consumable quantity is their COALESCE, floored
# separately below (codex #347: component tripwires alone would let a
# collapsed union print PASS). int_exp is floored at its (known-sparse)
# observed minimum for completeness; the charter fixed the C2 interest term to
# fin_exp.
# Re-derived after fix-financial-ingest-ambiguous-duplicates closed 26 of the
# 27 Step-A ingest holes (income 17->0, balancesheet 7->1, cashflow 3->0):
# every floor TIGHTENED or held — none loosened (a loosening would have
# signalled a regression, not a fix).
COVERAGE_FLOORS: Final[dict[str, float]] = {
    "revenue": 0.98,
    "total_revenue": 0.98,
    "oper_cost": 0.97,
    "sell_exp": 0.96,
    "admin_exp": 0.98,
    "rd_exp": 0.90,
    "int_exp": 0.05,
    "fin_exp": 0.97,
    "total_assets": 0.98,
    "total_hldr_eqy_inc_min_int": 0.98,
    "total_hldr_eqy_exc_min_int": 0.98,
    "accounts_receiv": 0.94,
    "inventories": 0.95,
    "prepayment": 0.96,
    "accounts_pay": 0.95,
    "adv_receipts": 0.31,
    "contract_liab": 0.21,
    "n_cashflow_act": 0.98,
}

# The C3-consumable adv_receipts∪contract_liab COALESCE floor (same rule:
# min 2019-2025 yearly mean 98.4% -> 0.98 - 0.02). Guarded explicitly because
# the component floors are regime-level tripwires only: a future ingest that
# collapses the union while both components stay above their (low) floors
# would otherwise pass unnoticed (codex #347).
ADV_CONTRACT_COALESCE_FLOOR: Final[float] = 0.96

# Provenance of the measured floors (report path + measurement rule).
FLOOR_PROVENANCE: Final[str] = (
    "docs/research/gate3_step_a_pit_coverage_report.md — "
    "floor = min(yearly mean as-of coverage, 2019-2025) - 0.02, ex-financial "
    "CSI300 members, quarterly snapshots, disclosure-of-record serve-rule"
)

# ---------------------------------------------------------------------------
# CSI800 (the fundamentals direction's universe)
# ---------------------------------------------------------------------------
# Measured on the CSI800-ever store after the 2026-08-13 ingest extended it
# from 627 to 2142 issuers, by the SAME rule as the CSI300 set above. Kept
# SEPARATE (never merged into / relaxed onto COVERAGE_FLOORS) so each universe
# keeps a tripwire calibrated on its own issuer mix.
#
# Measured outcome, recorded because it inverts the prior expectation that
# mid/small caps would disclose worse: 14 of 19 CSI800 floors are TIGHTER than
# or equal to their CSI300 counterparts (accounts_receiv 0.94->0.96,
# accounts_pay/inventories 0.95->0.96, oper_cost/fin_exp 0.97->0.98,
# prepayment 0.96->0.97, adv_receipts 0.31->0.34, coalesce 0.96->0.97). Only
# three are looser, each a KNOWN-WEAK field in a known-weak year rather than a
# breadth problem:
#   * contract_liab 0.21 -> 0.12  — 2019 is pre-2020 预收→合同负债 transition
#     (13.6% CSI800 vs 23.3% CSI300); both universes reach ~98% from 2021, and
#     the C3-consumable quantity is the COALESCE, which TIGHTENS to 0.97.
#   * rd_exp        0.90 -> 0.87  — 2019 edge (89.3% vs 92.3%); 2018 is
#     excluded by the rule in both universes (batch disclosure began 2018Q3).
#   * int_exp       0.05 -> 0.03  — an annual-report-only line that is already
#     unusable in BOTH universes (<10%); the charter fixed the C2 interest term
#     to fin_exp precisely because of this.
CSI800_COVERAGE_FLOORS: Final[dict[str, float]] = {
    "revenue": 0.98,
    "total_revenue": 0.98,
    "oper_cost": 0.98,
    "sell_exp": 0.96,
    "admin_exp": 0.98,
    "rd_exp": 0.87,
    "int_exp": 0.03,
    "fin_exp": 0.98,
    "total_assets": 0.98,
    "total_hldr_eqy_inc_min_int": 0.98,
    "total_hldr_eqy_exc_min_int": 0.98,
    "accounts_receiv": 0.96,
    "inventories": 0.96,
    "prepayment": 0.97,
    "accounts_pay": 0.96,
    "adv_receipts": 0.34,
    "contract_liab": 0.12,
    "n_cashflow_act": 0.98,
}

# The CSI800 adv_receipts∪contract_liab COALESCE floor (same separate-guard
# rationale as the CSI300 one; measured min 98.73% -> 0.99 - 0.02).
CSI800_ADV_CONTRACT_COALESCE_FLOOR: Final[float] = 0.97

CSI800_FLOOR_PROVENANCE: Final[str] = (
    "measured 2026-08-13 on the CSI800-ever store (2142 issuers, "
    "6415 parquet files) — floor = min(yearly mean as-of coverage, "
    "2019-2025) - 0.02, ex-financial CSI800 members, quarterly snapshots, "
    "disclosure-of-record serve-rule (same rule as FLOOR_PROVENANCE)"
)
