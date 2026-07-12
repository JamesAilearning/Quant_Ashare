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
"""
from __future__ import annotations

from typing import Final

# field -> minimum acceptable as-of coverage fraction (ex-financial members).
# Values = round(min over 2019-2025 of yearly mean as-of coverage, 2) - 0.02,
# from the Step-A report tables. adv_receipts / contract_liab floors are LOW by
# regime (the 2020 预收→合同负债 reclassification splits disclosure between
# them); the candidate-consumable quantity is their COALESCE (~98-99% every
# year, table §1) — these two floors are corruption tripwires, and the coalesce
# is guarded where it is computed (the Gate-3 evaluator). int_exp is floored at
# its (known-sparse) observed minimum for completeness; the charter fixed the
# C2 interest term to fin_exp.
COVERAGE_FLOORS: Final[dict[str, float]] = {
    "revenue": 0.95,
    "total_revenue": 0.95,
    "oper_cost": 0.95,
    "sell_exp": 0.94,
    "admin_exp": 0.95,
    "rd_exp": 0.88,
    "int_exp": 0.05,
    "fin_exp": 0.95,
    "total_assets": 0.97,
    "total_hldr_eqy_inc_min_int": 0.97,
    "total_hldr_eqy_exc_min_int": 0.97,
    "accounts_receiv": 0.94,
    "inventories": 0.95,
    "prepayment": 0.96,
    "accounts_pay": 0.95,
    "adv_receipts": 0.31,
    "contract_liab": 0.21,
    "n_cashflow_act": 0.97,
}

# Provenance of the measured floors (report path + measurement rule).
FLOOR_PROVENANCE: Final[str] = (
    "docs/research/gate3_step_a_pit_coverage_report.md — "
    "floor = min(yearly mean as-of coverage, 2019-2025) - 0.02, ex-financial "
    "CSI300 members, quarterly snapshots, disclosure-of-record serve-rule"
)
